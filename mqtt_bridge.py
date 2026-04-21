import sys, time, socket, yaml, threading, queue
import paho.mqtt.client as mqtt

# 1. 載入配置
with open("config.yaml", "r", encoding="utf-8") as f:
    raw_config = yaml.safe_load(f)

GATEWAY_IP = raw_config['gateway']['ip']
NAS_IP = "192.168.X.X"  # ⚠️ 请替换为你部署 MQTT 邮局的 NAS/服务器 IP

light_map = {(d['dev_no'], d['dev_ch']): d['name'] for d in raw_config.get('lights', [])}
ac_map = {(d['dev_no'], d['dev_ch']): d['name'] for d in raw_config.get('ac', [])}
heat_map = {(d['dev_no'], d['dev_ch']): d['name'] for d in raw_config.get('heating', [])}
fa_config = raw_config.get('fresh_air')

cmd_queue = queue.Queue()
gw_sock = None            
sock_lock = threading.Lock()

def calc_checksum(data): return sum(data) & 0xFF

def build_light(no, ch, on):
    d = [0xA9, 0x20, 0x01, no, 0x00, ch, 0x01 if on else 0x02, 0x00, 0x00]
    d.append(calc_checksum(d)); return bytes(d)

def build_fa(no, ch, cmd, speed=0):
    if cmd == 0x12: d = [0xA9, 0x20, 0x08, no, 0x59, ch, 0x12, 0x00, 0x02, 0x00, speed]
    else: d = [0xA9, 0x20, 0x08, no, 0x59, ch, cmd, 0x00, 0x02, 0x00, 0x00]
    d.append(calc_checksum(d)); return bytes(d)

def build_hvac(no, pid, ch, cmd, p1=0, p2=0):
    d = [0xA9, 0x20, 0x08, no, pid, ch, cmd, 0x00, 0x02, p1, p2]
    d.append(calc_checksum(d)); return bytes(d)

def parse_gateway_data(data, client):
    if len(data) < 10 or data[0] != 0xA9: return
    try:
        t, no, pid, ch, cmd = data[2], data[3], data[4], data[5], data[6]
        
        # 燈光同步 (包含 FE/FD 密碼本)
        if t == 0x01 and (no, ch) in light_map:
            name = light_map[(no, ch)]
            if cmd in (0x01, 0xFE): 
                client.publish(f"dnake/light/{name}/state", "ON", retain=True)
            elif cmd in (0x02, 0xFD): 
                client.publish(f"dnake/light/{name}/state", "OFF", retain=True)
            
        # 擴展設備同步
        elif t == 0x08:
            # 🌟 空調全功能同步 🌟
            if pid == 0x19 and (no, ch) in ac_map:
                name = ac_map[(no, ch)]
                if cmd == 1: 
                    pass 
                elif cmd == 2: 
                    client.publish(f"dnake/ac/{name}/mode/state", "off", retain=True)
                elif cmd == 0x10 and len(data)>=12: 
                    temp = ((data[9]<<8) | data[10])/10.0
                    client.publish(f"dnake/ac/{name}/temp/state", str(temp), retain=True)
                elif cmd == 0x11 and len(data)>=12: 
                    mode_map = {0: "cool", 1: "heat", 2: "fan_only", 3: "dry"}
                    if data[10] in mode_map:
                        client.publish(f"dnake/ac/{name}/mode/state", mode_map[data[10]], retain=True)
                elif cmd == 0x12 and len(data)>=12: 
                    fan_map = {1: "low", 2: "mid", 3: "high", 5: "auto"}
                    if data[10] in fan_map:
                        client.publish(f"dnake/ac/{name}/fan/state", fan_map[data[10]], retain=True)
                    
            # 地暖同步
            elif pid == 0xF1 and (no, ch) in heat_map:
                name = heat_map[(no, ch)]
                if cmd == 1: client.publish(f"dnake/heating/{name}/mode/state", "heat", retain=True)
                elif cmd == 2: client.publish(f"dnake/heating/{name}/mode/state", "off", retain=True)
                elif cmd == 0x10 and len(data)>=12:
                    temp = ((data[9]<<8) | data[10])/10.0
                    client.publish(f"dnake/heating/{name}/temp/state", str(temp), retain=True)
                    
            # 新風同步
            elif pid == 0x59 and fa_config and no == fa_config['dev_no'] and ch == fa_config['dev_ch']:
                if cmd == 1: client.publish("dnake/fresh_air/state", "ON", retain=True)
                elif cmd == 2: client.publish("dnake/fresh_air/state", "OFF", retain=True)
                elif cmd == 0x12 and len(data)>=12:
                    sm = {1:"low", 2:"mid", 3:"high"}
                    if data[10] in sm: client.publish("dnake/fresh_air/speed/state", sm[data[10]], retain=True)
    except Exception as e:
        pass

def listener_worker(client):
    global gw_sock
    while True:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(20) 
            s.connect((GATEWAY_IP, 4196))
            with sock_lock: gw_sock = s
            print("✅ 網關全雙工已連線，空調大滿貫功能就緒！", flush=True)
            
            def heartbeat():
                while gw_sock == s:
                    cmd_queue.put(bytes([0xA9, 0x20, 0x30, 0x01, 0x00, 0x00, 0x01, 0x00, 0x00, 0x0B]))
                    time.sleep(15)
            threading.Thread(target=heartbeat, daemon=True).start()

            while True:
                try:
                    data = s.recv(1024)
                    if not data: break
                    parse_gateway_data(data, client)
                except socket.timeout:
                    continue
        except Exception as e:
            time.sleep(5)
        finally:
            with sock_lock:
                if gw_sock == s: gw_sock = None
            s.close()
            time.sleep(3)

def sender_worker():
    while True:
        packet = cmd_queue.get()
        with sock_lock: s = gw_sock
        if s:
            try:
                s.sendall(packet)
                time.sleep(0.3) 
            except: pass
        cmd_queue.task_done()

def on_message(client, userdata, msg):
    topic, payload = msg.topic, msg.payload.decode()
    if "/state" in topic: return
    
    try:
        client.publish(topic.replace("/set", "/state"), payload, retain=True)
        
        if "light" in topic:
            name = topic.split("/")[2]
            dev = next((d for d in raw_config['lights'] if d['name'] == name), None)
            if dev: cmd_queue.put(build_light(dev['dev_no'], dev['dev_ch'], payload=="ON"))
            
        elif "fresh_air" in topic:
            if "speed" in topic:
                sm = {"low": 1, "mid": 2, "high": 3}
                if payload in sm: cmd_queue.put(build_fa(fa_config['dev_no'], fa_config['dev_ch'], 0x12, sm[payload]))
            else:
                cmd_queue.put(build_fa(fa_config['dev_no'], fa_config['dev_ch'], 1 if payload=="ON" else 2))
                
        elif "/ac/" in topic:
            name = topic.split("/")[2]
            dev = next((d for d in raw_config['ac'] if d['name'] == name), None)
            if dev:
                if "mode" in topic:
                    if payload == "off":
                        cmd_queue.put(build_hvac(dev['dev_no'], 0x19, dev['dev_ch'], 0x02))
                    else:
                        cmd_queue.put(build_hvac(dev['dev_no'], 0x19, dev['dev_ch'], 0x01))
                        mode_map = {"cool": 0, "heat": 1, "fan_only": 2, "dry": 3}
                        if payload in mode_map:
                            cmd_queue.put(build_hvac(dev['dev_no'], 0x19, dev['dev_ch'], 0x11, 0x00, mode_map[payload]))
                elif "temp" in topic:
                    t = int(float(payload)*10)
                    cmd_queue.put(build_hvac(dev['dev_no'], 0x19, dev['dev_ch'], 0x10, (t>>8)&0xFF, t&0xFF))
                elif "fan" in topic:
                    fan_map = {"low": 1, "mid": 2, "high": 3, "auto": 5}
                    if payload in fan_map:
                        cmd_queue.put(build_hvac(dev['dev_no'], 0x19, dev['dev_ch'], 0x12, 0x00, fan_map[payload]))
                    
        elif "/heating/" in topic:
            name = topic.split("/")[2]
            dev = next((d for d in raw_config['heating'] if d['name'] == name), None)
            if dev:
                if "mode" in topic: cmd_queue.put(build_hvac(dev['dev_no'], 0xF1, dev['dev_ch'], 1 if payload=="heat" else 2))
                elif "temp" in topic:
                    t = int(float(payload)*10)
                    cmd_queue.put(build_hvac(dev['dev_no'], 0xF1, dev['dev_ch'], 0x10, (t>>8)&0xFF, t&0xFF))
                    
    except Exception as e: print(f"❌ 解析錯誤: {e}", flush=True)

client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
client.on_connect = lambda c,u,f,rc,p: c.subscribe("dnake/#") if rc==0 else None
client.on_message = on_message

threading.Thread(target=listener_worker, args=(client,), daemon=True).start()
threading.Thread(target=sender_worker, daemon=True).start()

client.connect(NAS_IP, 1883)
client.loop_forever()