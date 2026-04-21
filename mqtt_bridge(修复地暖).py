import sys, time, socket, yaml, threading, queue
import paho.mqtt.client as mqtt

with open("config.yaml", "r", encoding="utf-8") as f:
    raw_config = yaml.safe_load(f)

GATEWAY_IP = raw_config['gateway']['ip']
NAS_IP = "192.168.22.121" 

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
        
        # 💡 全通道監聽：為了找出書房的真實通道
        if t == 0x08:
            hex_str = " ".join([f"{x:02X}" for x in data])
            print(f"📦 [網關回傳] PID:{pid:02X} 通道:{ch:02X} 指令:{cmd:02X} | {hex_str}", flush=True)

        if t == 0x01 and (no, ch) in light_map:
            name = light_map[(no, ch)]
            state = "ON" if cmd in (0x01, 0xFE) else "OFF"
            client.publish(f"dnake/light/{name}/state", state, retain=True)
            
        elif t == 0x08 and pid == 0x19 and (no, ch) in ac_map:
            name = ac_map[(no, ch)]
            if cmd == 0x02: client.publish(f"dnake/ac/{name}/mode/state", "off", retain=True)
            elif cmd == 0x10 and len(data)>=12:
                temp = ((data[9]<<8)|data[10])/10.0
                client.publish(f"dnake/ac/{name}/temp/state", str(temp), retain=True)
            elif cmd == 0x11 and len(data)>=12: 
                m = {0:"cool", 1:"heat", 2:"fan_only", 3:"dry"}
                if data[10] in m: client.publish(f"dnake/ac/{name}/mode/state", m[data[10]], retain=True)
            elif cmd == 0x12 and len(data)>=12: 
                f = {1:"low", 2:"mid", 3:"high", 5:"auto"}
                if data[10] in f: client.publish(f"dnake/ac/{name}/fan/state", f[data[10]], retain=True)
                
        elif t == 0x08 and pid == 0xF1 and (no, ch) in heat_map:
            name = heat_map[(no, ch)]
            if cmd in (0x11, 0x01, 0x64): client.publish(f"dnake/heating/{name}/mode/state", "heat", retain=True)
            elif cmd in (0x12, 0x02): client.publish(f"dnake/heating/{name}/mode/state", "off", retain=True)
            elif cmd in (0x10, 0x66) and len(data)>=12:
                temp = ((data[9]<<8)|data[10])/10.0
                client.publish(f"dnake/heating/{name}/temp/state", str(temp), retain=True)

        elif t == 0x08 and pid == 0x59 and fa_config and no == fa_config['dev_no'] and ch == fa_config['dev_ch']:
            if cmd == 1: client.publish("dnake/fresh_air/state", "ON", retain=True)
            elif cmd == 2: client.publish("dnake/fresh_air/state", "OFF", retain=True)
            elif cmd == 0x12 and len(data)>=12:
                sm = {1:"low", 2:"mid", 3:"high"}
                if data[10] in sm: client.publish("dnake/fresh_air/speed/state", sm[data[10]], retain=True)
    except Exception: pass

def on_message(client, userdata, msg):
    topic, payload = msg.topic, msg.payload.decode()
    if "/state" in topic: return
    print(f"📩 [MQTT收信] {topic} -> {payload}", flush=True)
    try:
        client.publish(topic.replace("/set", "/state"), payload, retain=True)
        
        if "/light/" in topic:
            name = topic.split("/")[2]
            dev = next((d for d in raw_config['lights'] if d['name'] == name), None)
            if dev: cmd_queue.put(build_light(dev['dev_no'], dev['dev_ch'], payload=="ON"))
            
        elif "/ac/" in topic:
            name = topic.split("/")[2]
            dev = next((d for d in raw_config['ac'] if d['name'] == name), None)
            if dev:
                if "mode" in topic:
                    cmd_queue.put(build_hvac(dev['dev_no'], 0x19, dev['dev_ch'], 0x02 if payload=="off" else 0x01))
                    if payload != "off":
                        m = {"cool":0, "heat":1, "fan_only":2, "dry":3}.get(payload, 0)
                        cmd_queue.put(build_hvac(dev['dev_no'], 0x19, dev['dev_ch'], 0x11, 0, m))
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
                if "mode" in topic:
                    cmd = 0x01 if payload == "heat" else 0x02
                    print(f"🔥 [發送地暖指令] {name} -> 0x{cmd:02X} (控制碼)", flush=True)
                    # 1. 發送控制指令
                    cmd_queue.put(build_hvac(dev['dev_no'], 0xF1, dev['dev_ch'], cmd))
                    # 2. 💡 修復客廳屏幕不聯動：發送 0x64 強制查詢狀態，逼迫網關更新屏幕
                    cmd_queue.put(build_hvac(dev['dev_no'], 0xF1, dev['dev_ch'], 0x64))
                elif "temp" in topic:
                    t = int(float(payload)*10)
                    cmd_queue.put(build_hvac(dev['dev_no'], 0xF1, dev['dev_ch'], 0x10, (t>>8)&0xFF, t&0xFF))
                    
        elif "fresh_air" in topic:
            if "speed" in topic:
                sm = {"low": 1, "mid": 2, "high": 3}
                if payload in sm: cmd_queue.put(build_fa(fa_config['dev_no'], fa_config['dev_ch'], 0x12, sm[payload]))
            else:
                cmd_queue.put(build_fa(fa_config['dev_no'], fa_config['dev_ch'], 1 if payload=="ON" else 2))
    except Exception as e: print(f"❌ 指令處理失敗: {e}", flush=True)

def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        print("✅ [MQTT] 已成功連線至 NAS Broker，開始監聽 HA 指令！", flush=True)
        client.subscribe("dnake/#")
    else:
        print(f"❌ [MQTT] 連線失敗，錯誤碼: {reason_code}", flush=True)

def listener_worker(client):
    global gw_sock
    while True:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(20); s.connect((GATEWAY_IP, 4196))
            with sock_lock: gw_sock = s
            print("✅ [網關] 狄耐克屏幕連線成功！全屋翻譯官已就位...", flush=True)
            
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
                except socket.timeout: continue
        except Exception: time.sleep(10)
        finally:
            with sock_lock: gw_sock = None
            s.close()

def sender_worker():
    while True:
        packet = cmd_queue.get()
        with sock_lock: s = gw_sock
        if s:
            try: s.sendall(packet); time.sleep(0.3)
            except: pass
        cmd_queue.task_done()

client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
client.on_connect = on_connect 
client.on_message = on_message
threading.Thread(target=listener_worker, args=(client,), daemon=True).start()
threading.Thread(target=sender_worker, daemon=True).start()
client.connect(NAS_IP, 1883)
client.loop_forever()
