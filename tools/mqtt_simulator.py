"""
MQTT 模拟传感器 — 用于测试企业碳账户系统的 MQTT 数据采集功能。

持续向 MQTT Broker 发布递增的传感器数据（模拟空压站累计产气量和耗电量），
企业碳账户系统的 MQTTAdapter 订阅后，DataCollector 自动采集并计算碳减排量。

═══════════════════════════════════════════════════════════════

使用方法:
    1. 确保已安装 paho-mqtt:
       pip install paho-mqtt

    2. 启动 MQTT Broker（如 mosquitto），或用公共测试 Broker

    3. 运行模拟器:
       python tools/mqtt_simulator.py

    4. 可选参数:
       python tools/mqtt_simulator.py --host localhost --port 1883 --topic enterprise/test/data --interval 5

    5. 在企业碳账户系统中配置项目的 MQTT 数据源:
       {
           "broker_host": "localhost",
           "broker_port": 1883,
           "topic": "enterprise/test/data"
       }

═══════════════════════════════════════════════════════════════

发布的消息格式 (JSON):
{
    "cumulative_flow": 5123.45,       # 累计产气量 m³（持续递增）
    "cumulative_power": 205.67,       # 累计耗电量 kWh（持续递增）
    "timestamp": "2026-06-11 14:30:00"
}
"""

import argparse
import json
import random
import signal
import sys
import time
from datetime import datetime


def main():
    parser = argparse.ArgumentParser(
        description="MQTT 模拟传感器 — 持续发布递增的传感器数据"
    )
    parser.add_argument("--host", default="localhost",
                        help="MQTT Broker 地址 (默认: localhost)")
    parser.add_argument("--port", type=int, default=1883,
                        help="MQTT Broker 端口 (默认: 1883)")
    parser.add_argument("--topic", default="enterprise/test/air_station/data",
                        help="发布主题 (默认: enterprise/test/air_station/data)")
    parser.add_argument("--interval", type=float, default=5.0,
                        help="发布间隔 秒 (默认: 5)")
    parser.add_argument("--qos", type=int, default=1,
                        help="MQTT QoS (默认: 1)")
    parser.add_argument("--username", default=None, help="MQTT 用户名")
    parser.add_argument("--password", default=None, help="MQTT 密码")
    parser.add_argument("--flow-start", type=float, default=5000.0,
                        help="累计产气量初始值 m³ (默认: 5000)")
    parser.add_argument("--power-start", type=float, default=200.0,
                        help="累计耗电量初始值 kWh (默认: 200)")
    parser.add_argument("--flow-delta-min", type=float, default=10.0,
                        help="每次产气增量下限 m³ (默认: 10)")
    parser.add_argument("--flow-delta-max", type=float, default=50.0,
                        help="每次产气增量上限 m³ (默认: 50)")
    parser.add_argument("--power-delta-min", type=float, default=0.5,
                        help="每次耗电增量下限 kWh (默认: 0.5)")
    parser.add_argument("--power-delta-max", type=float, default=2.0,
                        help="每次耗电增量上限 kWh (默认: 2.0)")
    parser.add_argument("--no-increment", action="store_true",
                        help="不递增，每次发送随机值（模拟非累计传感器）")

    args = parser.parse_args()

    # ── 检查依赖 ──
    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        print("❌ 未安装 paho-mqtt，请执行: pip install paho-mqtt")
        sys.exit(1)

    # ── 累计状态 ──
    state = {
        "cumulative_flow": args.flow_start,
        "cumulative_power": args.power_start,
    }
    msg_count = 0

    # ── 连接回调 ──
    def on_connect(client, userdata, flags, rc):
        if rc == 0:
            print(f"✅ 已连接到 MQTT Broker: {args.host}:{args.port}")
            print(f"📤 发布主题: {args.topic}")
            print(f"⏱  发布间隔: {args.interval}s")
            print(f"📊 初始值: flow={args.flow_start}, power={args.power_start}")
            print(f"{'🔄 非累计模式' if args.no_increment else '📈 累计递增模式'}")
            print("-" * 50)
        else:
            print(f"❌ 连接失败，返回码: {rc}")
            sys.exit(1)

    def on_disconnect(client, userdata, rc):
        print("⚠️  已断开连接")

    # ── 创建客户端 ──
    client = mqtt.Client()
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect

    if args.username:
        client.username_pw_set(args.username, args.password)

    try:
        client.connect(args.host, args.port, keepalive=60)
    except Exception as e:
        print(f"❌ 无法连接到 MQTT Broker {args.host}:{args.port}: {e}")
        sys.exit(1)

    client.loop_start()

    # ── 优雅退出 ──
    running = True

    def shutdown(signum, frame):
        nonlocal running
        print("\n🛑 收到停止信号，正在退出...")
        running = False

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # ── 发布循环 ──
    print("🚀 开始发布数据 (Ctrl+C 停止)...\n")

    while running:
        # 生成数据
        if args.no_increment:
            # 非累计模式：随机值
            state["cumulative_flow"] = random.uniform(5000, 5500)
            state["cumulative_power"] = random.uniform(200, 220)
        else:
            # 累计递增模式
            flow_delta = random.uniform(args.flow_delta_min, args.flow_delta_max)
            power_delta = random.uniform(args.power_delta_min, args.power_delta_max)
            state["cumulative_flow"] += flow_delta
            state["cumulative_power"] += power_delta

        payload = {
            "cumulative_flow": round(state["cumulative_flow"], 2),
            "cumulative_power": round(state["cumulative_power"], 2),
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

        # 发布
        msg_count += 1
        topic = args.topic
        json_payload = json.dumps(payload, ensure_ascii=False)
        result = client.publish(topic, json_payload, qos=args.qos)

        if result.rc == mqtt.MQTT_ERR_SUCCESS:
            print(f"  [#{msg_count:04d}] → {topic}")
            print(f"           flow={payload['cumulative_flow']:.2f} m³  "
                  f"power={payload['cumulative_power']:.2f} kWh")
        else:
            print(f"  [#{msg_count:04d}] ❌ 发布失败: rc={result.rc}")

        # 等待下一次发布
        time.sleep(args.interval)

    # ── 清理 ──
    print(f"\n📊 共发布 {msg_count} 条消息")
    client.loop_stop()
    client.disconnect()
    print("👋 模拟器已停止")


if __name__ == "__main__":
    main()
