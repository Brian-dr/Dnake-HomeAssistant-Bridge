# Dnake-HomeAssistant-Bridge

分享给沁兰园的邻居们，把开发商交付智能面板上的灯和空调地暖新风三件套全部接入Home Assistant实现双向同步和远程控制（内网穿透/tailscale下可以实现远程开关）

同样的方法可能也适用于繁华三章其他两个兄弟小区的朋友们，但需要根据自己家情况摸索微调一下~

在此特别感谢jupiter2021的启发，本项目借鉴了以下项目的一部分思路。

源项目地址：https://github.com/jupiter2021/smart-home-zigbee

# 🏠 绿城沁兰园狄耐克 (Dnake) 智能家居接入 Home Assistant 教程示例

## 📝 第 1 步：配置你家的“专属设备密码本”

由于我们每家每户的灯具数量、插线方式都不一样，在正式部署之前，我们需要把配置文件里的“通用设备编号”，替换成你家真实的“专属密码”。

我在仓库里提供了一个叫 `device_bean.csv` 的参考文件（这其实是从狄耐克网关里导出的设备清单）。**你需要获取你自己家的这份表格。**

以下示例为我家100平户型的设备，135和177户型的邻居需要根据自家智能网关里的device_bean列表添加额外的设备进config.yaml文件里。

具体拉取自家device_bean的方式可以参考jupiter2021大佬的源项目。

https://github.com/jupiter2021/smart-home-zigbee

打开你家的 `device_bean.csv`，对照着修改 `config.yaml` 里面的设备信息。（省力的方式可以把自家的device_bean.csv和config.yaml同时喂给ai，让他帮你自动生成个新的config.yaml配置）

### 🔍 怎么看懂表格并修改？（包教包会）

在 CSV 表格里，你需要重点关注这三列：
* `DEV_NAME` (设备名称)：比如“客厅主灯”
* `DEV_NO` (设备组号)：比如 `51`
* `DEV_CH` (设备通道号)：比如 `02`

**【修改对照示例】**

如果你在 CSV 表格里看到这样一行：
> ... , **客厅主灯** , 01, **51**, 00, **02** , 0

那么在你的 `config.yaml` 文件里，你要把它写成这样（注意：要在数字前面加上 `0x` 代表十六进制）：
```yaml
  - name: "客厅主灯"
    dev_no: 0x51   # 对应表格里的 51
    dev_ch: 0x02   # 对应表格里的 02
```

## 🛠️ 第2步：Docker 部署 (环境搭建)

我建议使用 Docker 进行部署，既干净又不会弄乱你的NAS/服务器环境。

### 1. 部署 MQTT 邮局 (mqtt_broker)
这是全屋设备交换信息的“中转站”。
* 在 NAS/服务器 docker文件夹下创建 `mosquitto` 目录。
* **核心避坑：** 务必在终端执行 `chmod -R 777 /your_path/mosquitto/data` 赋予写入权限，否则docker重启后HA里设备状态会丢失同步！！
* 运行 `docker-compose` 启动服务。

示例（加了日志文件上限，防止占用过多存储空间，可根据自己实际情况更改）：
 ```yaml
 version: '3.8'

 services:
   mosquitto:
     image: eclipse-mosquitto:latest
     container_name: mqtt_broker
     restart: unless-stopped
     ports:
       - "1883:1883"
     volumes:
       - ./config/mosquitto.conf:/mosquitto/config/mosquitto.conf
       - ./data:/mosquitto/data
       - ./log:/mosquitto/log
     logging:
       driver: "json-file"
       options:
         max-size: "10m"   # 限制单个日志文件最大 10MB
         max-file: "3"     # 最多保留 3 个备份  
     environment:
       - TZ=Asia/Shanghai
   ```   

### 2. 部署网桥程序 (dnake_zigbee)
这是负责和狄耐克屏幕“聊天”的翻译程序。
* 在 NAS/服务器 docker文件夹下创建 `zigbee` 目录。
* 下载本仓库的 `mqtt_bridge.py` 和 `config.yaml`。
* 修改 `config.yaml` 里的 IP 地址为你家的真实地址。
* 使用仓库提供的 `docker-compose.yml` 启动，程序会自动处理指令排队，防止网关卡死。

示例（加了日志文件上限，防止占用过多存储空间，可根据自己实际情况更改）
 ```yaml
 version: '3.8'

 services:
   dnake-bridge:
     image: python:3.10-slim
     container_name: dnake_zigbee
     network_mode: "host"
     restart: unless-stopped
     environment:
       - TZ=Asia/Shanghai
       - PYTHONUNBUFFERED=1
     volumes:
      - .:/app
     working_dir: /app
     logging:
       driver: "json-file"
       options:
         max-size: "10m"   # 限制单个日志文件最大 10MB
         max-file: "3"     # 最多保留 3 个备份
     command: >   # 切换至清华源国内网络环境可以访问
       bash -c "
       pip install paho-mqtt pyyaml -i https://pypi.tuna.tsinghua.edu.cn/simple &&    
       python -u mqtt_bridge.py
       "
```
验证成功连接mqtt_bridge的方法，SSH访问NAS/服务器，输入下列代码，显示“ ✅ 網關全雙工已連線，空調大滿貫功能就緒！” 即表示部署成功。
```
docker logs -f dnake_zigbee
```

## 💡 第3步：Home Assistant 配置

### 🔌 1. 让 Home Assistant 连上“MQTT 邮局”

在刚才的第一步里，我们用 Docker 在NAS/服务器里建好了一个“MQTT 邮局”。现在，我们需要让 Home Assistant (HA) 知道这个邮局的地址，这样它才能顺利接收和发送狄耐克设备的情报。

**操作步骤（全程在 HA 网页端点选即可）：**

1. 打开你的 Home Assistant 网页控制台。
2. 点击左侧边栏的 **配置 (Settings)** -> **设备与服务 (Devices & Services)**。
3. 点击右下角的 **+ 添加集成 (Add Integration)**。
4. 在搜索框里输入 `MQTT`，然后点击搜索出来的 **MQTT** 官方图标。
5. 在弹出的配置窗口中，按以下信息填写：
   * **代理 (Broker)**：填入你家刚刚部署 MQTT Docker 的NAS/服务器局域网 IP 地址（例如 `192.168.x.x`）。
   * **端口 (Port)**：`1883`
   * **用户名 / 密码**：直接留空（因为我们的本地基础部署没有开启密码验证）。
6. 点击 **提交**。

🎉 只要屏幕上提示“成功”，就说明你的 HA 已经完美对接了本地的 MQTT 邮局！

---

### 📝 2. 把设备挂载到 Home Assistant 界面上

邮局虽然通了，但 HA 还没见过你家的设备清单。最后一步，就是把我们前面整理好的设备代码贴进去。

下载本项目的`configuration.yaml`文件，并按照下方示例格式，对照前文的 `config.yaml` 录入你家的设备，保存并将其上传至在部署HA的 NAS/服务器 项目目录，重启HA后就能在设备处刷出网关下的所有设备。

*(注意：下面示例代码里的设备名字，必须和你第一步在 `config.yaml` 里填写的名字一模一样！)*

```yaml
# ====== 空调组件 (Climate) ======
  climate:
    - name: "客厅空调"
      unique_id: "dnake_ac_living_room"
      modes: ["off", "cool", "heat", "dry", "fan_only"]  #对应关机、制冷、制热、抽湿、送风模式
      mode_state_topic: "dnake/ac/客厅空调/mode/state"
      mode_command_topic: "dnake/ac/客厅空调/mode/set"
      temperature_state_topic: "dnake/ac/客厅空调/temp/state"
      temperature_command_topic: "dnake/ac/客厅空调/temp/set"
      fan_modes: ["auto", "low", "mid", "high"]          #对应自动、低、中、高四档风速
      fan_mode_state_topic: "dnake/ac/客厅空调/fan/state"
      fan_mode_command_topic: "dnake/ac/客厅空调/fan/set"
      min_temp: 16
      max_temp: 32
      temp_step: 1

  # ====== 地暖组件 (Climate) ======
  # 地暖和空调类似，但模式通常只有关(off)和加热(heat)
    - name: "客厅地暖"
      unique_id: "dnake_heating_living_room"
      modes: ["off", "heat"]
      mode_state_topic: "dnake/heating/客厅地暖/mode/state"
      mode_command_topic: "dnake/heating/客厅地暖/mode/set"
      temperature_state_topic: "dnake/heating/客厅地暖/temp/state"
      temperature_command_topic: "dnake/heating/客厅地暖/temp/set"
      min_temp: 16
      max_temp: 32
      temp_step: 1

  # ====== 新风组件 (Fan) ======
  fan:
    - name: "新风系统"
      unique_id: "dnake_fresh_air_main"
      state_topic: "dnake/fresh_air/state"
      command_topic: "dnake/fresh_air/set"
      preset_mode_state_topic: "dnake/fresh_air/speed/state"
      preset_mode_command_topic: "dnake/fresh_air/speed/set"
      preset_modes:  #对应低、中、高三档风速
        - "low"
        - "mid"
        - "high"
      payload_on: "ON"
      payload_off: "OFF"
# ... 

```

## 👨‍🔬 常见问题 (邻居必读)
* **状态显示“未知”？** 别担心，去墙上按一下开关，状态就会立即同步并永久记录。
* **日志太多占空间？** 教程自带了日志自动清理功能，每 10MB 会自动覆盖旧日志，放心运行。
