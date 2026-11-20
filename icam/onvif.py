"""ONVIF 探索與取流（WS-Discovery + GetProfiles/GetStreamUri）。

只做監看需要的兩件事：找到區網的攝影機、問它 RTSP 網址。
"""

import re
import socket
import time
import uuid
import xml.etree.ElementTree as ET

NS = {
    "s": "http://www.w3.org/2003/05/soap-envelope",
    "tds": "http://www.onvif.org/ver10/device/wsdl",
    "trt": "http://www.onvif.org/ver10/media/wsdl",
    "tt": "http://www.onvif.org/ver10/schema",
}

PROBE = """<?xml version="1.0" encoding="UTF-8"?>
<e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope"
 xmlns:w="http://schemas.xmlsoap.org/ws/2004/08/addressing"
 xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery"
 xmlns:dn="http://www.onvif.org/ver10/network/wsdl">
 <e:Header>
  <w:MessageID>uuid:{mid}</w:MessageID>
  <w:To>urn:schemas-xmlsoap-org:ws:2005:04:discovery</w:To>
  <w:Action>http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</w:Action>
 </e:Header>
 <e:Body><d:Probe><d:Types>dn:NetworkVideoTransmitter</d:Types></d:Probe></e:Body>
</e:Envelope>"""

GET_STREAM_URI = """<?xml version="1.0" encoding="UTF-8"?>
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">
 <s:Body><GetStreamUri xmlns="http://www.onvif.org/ver10/media/wsdl">
  <StreamSetup>
   <Stream xmlns="http://www.onvif.org/ver10/schema">RTP-Unicast</Stream>
   <Transport xmlns="http://www.onvif.org/ver10/schema"><Protocol>RTSP</Protocol></Transport>
  </StreamSetup>
  <ProfileToken>{token}</ProfileToken>
 </GetStreamUri></s:Body>
</s:Envelope>"""


def _local_ipv4s():
    ip = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        ip.connect(("223.5.5.5", 80))
        primary = ip.getsockname()[0]
    except OSError:
        primary = "127.0.0.1"
    finally:
        ip.close()
    out = [primary]
    try:
        for line in socket.if_nameindex():
            pass
    except OSError:
        pass
    try:
        out += [a[4][0] for a in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)]
    except OSError:
        pass
    seen, uniq = set(), []
    for a in out:
        if a not in seen and not a.startswith("127."):
            seen.add(a)
            uniq.append(a)
    return uniq


def discover(timeout=5.0):
    """回傳 [{'ip','xaddr','name','hardware','location'}]。"""
    found = {}
    msg = PROBE.format(mid=uuid.uuid4()).encode()
    for iface in _local_ipv4s():
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        try:
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(iface))
        except OSError:
            s.close()
            continue
        s.settimeout(0.5)
        try:
            s.sendto(msg, ("239.255.255.250", 3702))
        except OSError:
            s.close()
            continue
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                data, addr = s.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break
            txt = data.decode("utf-8", "ignore")
            xaddrs = re.findall(r"<[^>]*XAddrs>(.*?)</[^>]*XAddrs>", txt)
            scopes = re.findall(r"<[^>]*Scopes>(.*?)</[^>]*Scopes>", txt)
            name = hardware = location = ""
            if scopes:
                for pair in scopes[0].split():
                    if "/name/" in pair:
                        name = pair.split("/name/", 1)[1]
                    elif "/hardware/" in pair:
                        hardware = pair.split("/hardware/", 1)[1]
                    elif "/location/" in pair:
                        location = pair.split("/location/", 1)[1]
            found[addr[0]] = {
                "ip": addr[0],
                "xaddr": xaddrs[0].split()[0] if xaddrs else "",
                "name": name or addr[0],
                "hardware": hardware,
                "location": location,
            }
        s.close()
    return [found[k] for k in sorted(found, key=lambda x: tuple(int(p) for p in x.split(".")))]


def _post(url, body, user=None, password=None):
    import requests
    auth = None
    if user:
        from requests.auth import HTTPDigestAuth, HTTPBasicAuth
        r = requests.post(url, data=body.encode(), timeout=6,
                          headers={"Content-Type": "application/soap+xml; charset=utf-8"})
        if r.status_code == 401:
            r = requests.post(url, data=body.encode(), timeout=6,
                              headers={"Content-Type": "application/soap+xml; charset=utf-8"},
                              auth=HTTPDigestAuth(user, password or ""))
            if r.status_code == 401:
                r = requests.post(url, data=body.encode(), timeout=6,
                                  headers={"Content-Type": "application/soap+xml; charset=utf-8"},
                                  auth=HTTPBasicAuth(user, password or ""))
        return r
    return requests.post(url, data=body.encode(), timeout=6,
                         headers={"Content-Type": "application/soap+xml; charset=utf-8"})


def get_stream_uri(xaddr, user, password):
    """回傳 (rtsp_url, 說明)。token 走第一個 profile。"""
    from requests.auth import HTTPDigestAuth
    profiles_body = """<?xml version="1.0" encoding="UTF-8"?>
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">
 <s:Body><GetProfiles xmlns="http://www.onvif.org/ver10/media/wsdl"/></s:Body></s:Envelope>"""
    r = _post(xaddr, profiles_body, user, password)
    if r.status_code != 200:
        return None, "GetProfiles HTTP %s" % r.status_code
    tokens = re.findall(r'ProfileToken="([^"]+)"', r.text) or \
             re.findall(r"<[^>]*ProfileToken>(.*?)</[^>]*ProfileToken>", r.text)
    if not tokens:
        return None, "沒有 profile"
    token = tokens[0]
    r = _post(xaddr, GET_STREAM_URI.format(token=token), user, password)
    if r.status_code != 200:
        return None, "GetStreamUri HTTP %s" % r.status_code
    m = re.search(r"<[^>]*Uri>(rtsp://[^<]+)</[^>]*Uri>", r.text)
    if not m:
        return None, "回應沒有 Uri"
    uri = m.group(1).strip()
    if user and "@" not in uri:
        uri = uri.replace("rtsp://", "rtsp://%s:%s@" % (user, password), 1)
    return uri, "ok"
