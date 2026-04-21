import sys, time, socket, yaml, threading, queue
import paho.mqtt.client as mqtt

with open("config.yaml", "r", encoding="utf-8") as f:
    raw_config = yaml.safe_load(f)

GATEWAY_IP = raw_config['gateway']['ip']
NAS_IP = "192.168.x.x"   #替换你自己的nas或者服务器ip地址

light_map = {(d['dev_no'], d['dev_ch']): d['name'] for d in raw_config.get('lights', [])}
ac_map = {(d['dev_no'], d['dev_ch']): d['name'] for d in raw_config.get('ac', [])}
heat_map = {(d['dev_no'], d['dev_ch']): d['name'] for d in raw_config.get('heating', [])}
fa_config = raw_config.get('fresh_air')

cmd_queue = queue.Queue()
gw_sock = None            
sock_lock = threading.Lock()

def calc_checksum(data): return sum(data) & 0xFF
def build_light(no, ch, on): d = [0xA9, 0x20, 0x01, no, 0x00, ch, 0x01 if on else 0x02, 0x00, 0x00]; d.append(calc_checksum(d)); return bytes(d)
def build_hvac(no, pid, ch, cmd, p1=0, p2=0): d = [0xA9, 0x20, 0x08, no, pid, ch, cmd, 0x00, 0x02, p1, p2]; d.append(calc_checksum(d)); return bytes(d)

def parse_gateway_data(data, client):
    if len(data) < 10 or data[0] != 0xA9: return
    try:
        t, no, pid, ch, cmd = data[2], data[3], data[4], data[5], data[6]
        
        # 1. 灯光同步
        if t == 0x01 and (no, ch) in light_map:
            client.publish(f"dnake/light/{light_map[(no, ch)]}/state", "ON" if cmd in (0x01, 0xFE) else "OFF", retain=True)
            
        # 2. 空调同步 (PID: 0x19)
        elif t == 0x08 and pid == 0x19 and (no, ch) in ac_map:
            name = ac_map[(no, ch)]
            if cmd == 0x02: client.publish(f"dnake/ac/{name}/mode/state", "off", retain=True)
            elif cmd == 0x10 and len(data)>=12: client.publish(f"dnake/ac/{name}/temp/state", str(((data[9]<<8)|data[10])/10.0), retain=True)
            elif cmd == 0x11 and len(data)>=12:
                m = {0:"cool", 1:"heat", 2:"fan_only", 3:"dry"}.get(data[10], "off")
                client.publish(f"dnake/ac/{name}/mode/state", m, retain=True)
                
        # 3. 地暖同步 (PID: 0xF1) - 包含 9B 状态反馈
        elif t == 0x08 and pid == 0xF1 and (no, ch) in heat_map:
            name = heat_map[(no, ch)]
            if cmd == 0x9B and len(data)>=12:
                client.publish(f"dnake/heating/{name}/mode/state", "heat" if data[10] == 1 else "off", retain=True)
            elif cmd in (0x11, 0x01, 0x64): client.publish(f"dnake/heating/{name}/mode/state", "heat", retain=True)
            elif cmd in (0x12, 0x02): client.publish(f"dnake/heating/{name}/mode/state", "off", retain=True)

        # 🚀 4. 新风同步解析 (修正 PID 为 0x59)
        elif t == 0x08 and pid == 0x59 and fa_config and no == fa_config['dev_no']:
            if cmd == 0x01: client.publish("dnake/fresh_air/state", "ON", retain=True)
            elif cmd == 0x02: client.publish("dnake/fresh_air/state", "OFF", retain=True)
            elif cmd == 0x9B and len(data)>=12:
                client.publish("dnake/fresh_air/state", "ON" if data[10] == 1 else "OFF", retain=True)
            elif cmd == 0x12 and len(data) >= 12:
                s_m = {0x01: "low", 0x02: "mid", 0x03: "high"}
                client.publish("dnake/fresh_air/speed/state", s_m.get(data[10], "low"), retain=True)

    except Exception: pass

def on_message(client, userdata, msg):
    topic, payload = msg.topic, msg.payload.decode()
    if "/state" in topic: return
    try:
        # UI 乐观更新
        client.publish(topic.replace("/set", "/state"), payload, retain=True)
        
        # 🚀 新风系统控制
        if "fresh_air" in topic:
            if not fa_config: return
            # 💡 致命陷阱修复：先判断是否是调风速，再判断是否是开关，防止冲突！
            if "/speed/set" in topic:
                s_val = {"low": 1, "mid": 2, "high": 3}.get(payload, 1)
                cmd_queue.put(build_hvac(fa_config['dev_no'], 0x59, fa_config['dev_ch'], 0x12, 0, s_val))
            elif topic.endswith("/set"):
                cmd = 0x01 if payload == "ON" else 0x02
                cmd_queue.put(build_hvac(fa_config['dev_no'], 0x59, fa_config['dev_ch'], cmd))
                cmd_queue.put(build_hvac(fa_config['dev_no'], 0x59, fa_config['dev_ch'], 0x64))

        # 地暖控制逻辑 (针对书房/次卧优化)
        elif "/heating/" in topic:
            name = topic.split("/")[2]
            dev = next((d for d in raw_config['heating'] if d['name'] == name), None)
            if dev:
                cmd = 0x01 if payload == "heat" else 0x02
                cmd_queue.put(build_hvac(dev['dev_no'], 0xF1, dev['dev_ch'], cmd))
                cmd_queue.put(build_hvac(dev['dev_no'], 0xF1, dev['dev_ch'], 0x64))
        
        # 灯光控制
        elif "/light/" in topic:
            name = topic.split("/")[2]
            dev = next((d for d in raw_config['lights'] if d['name'] == name), None)
            if dev: cmd_queue.put(build_light(dev['dev_no'], dev['dev_ch'], payload=="ON"))
            
        # 空调控制
        elif "/ac/" in topic:
            name = topic.split("/")[2]
            dev = next((d for d in raw_config['ac'] if d['name'] == name), None)
            if dev:
                if "mode" in topic:
                    cmd_queue.put(build_hvac(dev['dev_no'], 0x19, dev['dev_ch'], 0x02 if payload=="off" else 0x01))
    except Exception: pass

def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0: client.subscribe("dnake/#")

def listener_worker(client):
    global gw_sock
    while True:
        s = None  # 💡 修复网络波动导致的崩溃
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(20); s.connect((GATEWAY_IP, 4196))
            with sock_lock: gw_sock = s
            while True:
                data = s.recv(1024)
                if not data: break
                parse_gateway_data(data, client)
        except Exception: time.sleep(10)
        finally:
            with sock_lock: gw_sock = None
            if s:
                try: s.close()
                except: pass

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
