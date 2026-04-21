# Dnake-HomeAssistant-Bridge
分享给沁兰园的邻居们，把开发商交付智能面板上的灯和空调地暖新风三件套全部接入Home Assistant实现双向同步和远程控制（内网穿透/tailscale下可以实现远程开关）
同样的方法可能也适用于繁华三章其他两个兄弟小区的朋友们，但需要根据自己家情况摸索微调一下~

在此特别感谢jupiter2021的启发，本项目借鉴了部分思路。
源项目地址：https://github.com/jupiter2021/smart-home-zigbee

# 🏠 狄耐克 (Dnake) 智能家居接入 Home Assistant 计划

## 🛠️ 第一步：Docker 部署 (环境搭建)

我们建议使用 Docker 进行部署，既干净又不会弄乱你的 NAS/服务器 环境。

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
     command: >
       bash -c "
       pip install paho-mqtt pyyaml -i https://pypi.tuna.tsinghua.edu.cn/simple &&    # 切换至清华源国内网络环境可以访问
       python -u mqtt_bridge.py
       "

## 💡 第二步：Home Assistant 配置

1. 打开你的 HA 配置文件 `configuration.yaml`。
2. 将本仓库提供的 `configuration.yaml` 代码片段粘贴进去。
3. 重启 Home Assistant，你就能在界面上看到漂亮的空调面板和灯光开关了！

---

## 👨‍🔬 常见问题 (邻居必读)
* **状态显示“未知”？** 别担心，去墙上按一下开关，状态就会立即同步并永久记录。
* **日志太多占空间？** 教程自带了日志自动清理功能，每 10MB 会自动覆盖旧日志，放心运行。
