import re
import json
from typing import List, Dict, Any, Optional


def _is_truncated_output(value: str) -> bool:
    lowered = (value or '').lower()
    return any(
        marker in lowered
        for marker in (
            '... [truncated',
            '[error] command timed out',
            'results above may be incomplete',
            'timed out after',
            'partial_results',
        )
    )


def _extract_ip_from_header(header: str) -> Optional[str]:
    ip_match = re.search(r'\((\d{1,3}(?:\.\d{1,3}){3})\)', header)
    if ip_match:
        return ip_match.group(1)

    ip_only = re.match(r'^(\d{1,3}(?:\.\d{1,3}){3})$', header)
    if ip_only:
        return ip_only.group(1)

    return None


def _parse_compact_nmap_summary(text: str, max_hosts: int) -> Dict[str, Any]:
    lines = [line.rstrip() for line in text.splitlines()]
    hosts: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    seen = set()
    scanned = None
    up = None

    summary_match = re.search(r'Nmap summary:\s*scanned=(\d+),\s*up=(\d+)', text)
    if summary_match:
        scanned = int(summary_match.group(1))
        up = int(summary_match.group(2))
    else:
        detected_match = re.search(r'Nmap summary:\s*detected_up_hosts=(\d+)', text)
        if detected_match:
            up = int(detected_match.group(1))

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        if stripped.startswith('Nmap summary:'):
            continue
        if stripped.startswith('Authoritative note:'):
            continue
        if stripped.startswith('Note:'):
            continue
        if stripped.startswith('Up hosts'):
            continue
        if stripped.startswith('- ... and '):
            continue

        host_match = re.match(r'^-\s+(.+)$', stripped) if line == stripped else None
        if host_match:
            header = host_match.group(1).strip()
            if header.startswith('...'):
                continue
            key = header.lower()
            if key in seen:
                current = None
                continue

            current = {
                'host': header,
                'ip': _extract_ip_from_header(header),
                'ports': [],
                'mac': None,
                'service_info': None,
                'raw_lines': [line],
            }
            seen.add(key)
            hosts.append(current)
            continue

        if current is None:
            continue

        current['raw_lines'].append(line)

        if stripped.startswith('Host is up'):
            continue

        if stripped.startswith('MAC Address:'):
            parts = stripped.split('MAC Address:')[-1].strip()
            current['mac'] = parts.split()[0] if parts else None
            continue

        if stripped.startswith('Service Info:'):
            current['service_info'] = stripped.split('Service Info:')[-1].strip()
            continue

        port_match = re.match(r'^(\d+)/(tcp|udp)\s+(open|closed|filtered|open\|filtered)\s+(.+)$', stripped)
        if port_match:
            rest = port_match.group(4).strip()
            service = rest.split()[0] if rest else rest
            current['ports'].append({
                'port': int(port_match.group(1)),
                'proto': port_match.group(2),
                'state': port_match.group(3),
                'service': service,
                'version': None,
            })

    return {
        'hosts': hosts[:max_hosts],
        'scanned': scanned,
        'up': up if up is not None else (len(hosts) if hosts else None),
        'truncated': _is_truncated_output(text),
    }


def parse_nmap_to_json(raw_output: str, max_hosts: int = 500) -> Dict[str, Any]:
    """Parse Nmap plaintext output into a compact JSON host table.

    The returned structure is:
    {
      "hosts": [
         {"host": "hostname (ip)", "ip": "1.2.3.4", "ports": [{"port":80,"proto":"tcp","state":"open","service":"http","version":"..."}], "mac": "aa:bb:...", "service_info": "...", "raw_lines": [...]}
      ],
      "scanned": int|None,
      "up": int|None,
      "truncated": bool
    }
    """
    text = (raw_output or '')
    lines = [l.rstrip() for l in text.splitlines()]

    host_blocks: List[Dict[str, Any]] = []
    current = None
    current_lines: List[str] = []

    for line in lines:
        m = re.match(r'Nmap scan report for\s+(.+)', line)
        if m:
            if current is not None:
                host_blocks.append({'header': current, 'lines': current_lines})
            current = m.group(1).strip()
            current_lines = [line]
            continue
        if current is not None:
            current_lines.append(line)

    if current is not None:
        host_blocks.append({'header': current, 'lines': current_lines})

    if not host_blocks and re.search(r'^Nmap summary:\s*', text, flags=re.MULTILINE):
        return _parse_compact_nmap_summary(text, max_hosts=max_hosts)

    hosts: List[Dict[str, Any]] = []
    seen = set()
    for block in host_blocks[:max_hosts]:
        hdr = block.get('header') or ''
        if not hdr:
            continue
        key = hdr.lower()
        if key in seen:
            continue
        seen.add(key)

        entry: Dict[str, Any] = {'host': hdr, 'ip': None, 'ports': [], 'mac': None, 'service_info': None, 'raw_lines': block.get('lines', [])}

        entry['ip'] = _extract_ip_from_header(hdr)

        for ln in block.get('lines', []):
            ln = ln.strip()
            if ln.startswith('MAC Address:'):
                # e.g. MAC Address: aa:bb:cc:dd:ee:ff (Vendor)
                parts = ln.split('MAC Address:')[-1].strip()
                mac = parts.split()[0]
                entry['mac'] = mac
            if ln.startswith('Service Info:'):
                entry['service_info'] = ln.split('Service Info:')[-1].strip()
            # PORT lines like: 22/tcp open  ssh
            p = re.match(r'^(\d+)/(tcp|udp)\s+(open|closed|filtered|open\|filtered)\s+(.+)$', ln)
            if p:
                portnum = int(p.group(1))
                proto = p.group(2)
                state = p.group(3)
                rest = p.group(4).strip()
                # split service and possible version
                svc = rest
                version = None
                # some lines include extra columns; keep service as first token
                if '  ' in rest:
                    parts = [x for x in rest.split('  ') if x]
                    svc = parts[0]
                    if len(parts) > 1:
                        version = parts[1]
                else:
                    svc = rest.split()[0] if rest else rest

                entry['ports'].append({'port': portnum, 'proto': proto, 'state': state, 'service': svc, 'version': version})

        hosts.append(entry)

    # Extract Nmap done summary
    done_match = re.search(r'Nmap done:\s+(\d+)\s+IP addresses\s+\((\d+)\s+hosts up\)', text)
    scanned = int(done_match.group(1)) if done_match else None
    up = int(done_match.group(2)) if done_match else (len(hosts) if hosts else None)

    result: Dict[str, Any] = {
        'hosts': hosts,
        'scanned': scanned,
        'up': up,
        'truncated': _is_truncated_output(text),
    }

    return result


def nmap_json_to_summary(parsed: Dict[str, Any], max_hosts: int = 50) -> str:
    if not isinstance(parsed, dict):
        return 'Invalid parse result.'
    hosts = parsed.get('hosts') or []
    scanned = parsed.get('scanned')
    up = parsed.get('up')
    truncated = parsed.get('truncated')

    lines = []
    if scanned is not None:
        lines.append(f"Nmap summary: scanned={scanned}, up={up}")
    else:
        lines.append(f"Nmap summary: detected_up_hosts={len(hosts)}")

    lines.append('Authoritative note: only hosts and services present in the JSON table are confirmed.')

    for h in hosts[:max_hosts]:
        host = h.get('host')
        ip = h.get('ip')
        ports = h.get('ports') or []
        host_label = str(host or '')
        if ip and host_label and host_label != ip and not host_label.endswith(f'({ip})'):
            host_label = f"{host_label} ({ip})"
        lines.append(f"- {host_label}: {len(ports)} ports listed")
        for p in ports[:4]:
            lines.append(f"  - {p.get('port')}/{p.get('proto')} {p.get('state')} {p.get('service')}{(' '+(p.get('version') or '')) if p.get('version') else ''}")

    if len(hosts) > max_hosts:
        lines.append(f"- ... and {len(hosts)-max_hosts} more hosts")

    if truncated:
        lines.append('Note: The original Nmap output was truncated or incomplete.')

    return '\n'.join(lines)
