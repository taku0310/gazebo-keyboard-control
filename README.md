# gazebo-keyboard-control

キーボード入力で Gazebo Sim（Harmonic）シミュレーション内のロボットを操作する、
マルチコンテナ Docker + ROS 2 構成のデモです。差動二輪ロボットをキーボードで操作したり、
記録済みの JSON シナリオを再生して再現性のあるデモを実行できます。
macOS / WSL2 / Ubuntu で同じように動作します。

```
┌──────────────────┐   /cmd_vel    ┌───────────────┐  /gazebo/cmd_vel  ┌──────────┐
│ keyboard_         │ ───────────▶ │ control_logic │ ───────────────▶ │  gazebo  │
│ controller        │   (Twist)     │ (safety/      │     (Twist)       │ (Gazebo  │
│ (stdin/pynput→Twist)│             │  smoothing)   │                   │ Harmonic)│
└──────────────────┘               └───────────────┘                   └──────────┘
          │                                 │                                 │
          └─────────────────────────────────┴─────────────────────────────────┘
                       ROS 2 DDS discovery (masterless, ROS_DOMAIN_ID)
                                                          Web UI (noVNC) :8080
```

## コンポーネント構成

ROS 2 はマスターレスです（roscore コンテナはありません）。各ノードは同じ
`ROS_DOMAIN_ID` を共有し、`ros_net` 上で DDS により相互探索します。

| コンテナ | 役割 | 主要技術 |
|-----------|------|----------|
| `keyboard_controller` | キーボード入力 → `geometry_msgs/msg/Twist` を `/cmd_vel` に 20 Hz で発行 | Python, rclpy, stdin/pynput |
| `control_logic` | 安全制約 + 平滑化 → `/gazebo/cmd_vel` を発行 | Python, rclpy |
| `gazebo` | 3D 物理演算 + Web 可視化 | Gazebo Sim Harmonic, ros_gz bridge, noVNC |

ベースは ROS 2 Jazzy（Ubuntu 24.04）。

### データフロー

1. **keyboard_controller** がキー入力（または JSON シナリオ）を `Twist` メッセージに
   変換し、`/cmd_vel` へ 20 Hz で発行します。
2. **control_logic** が `/cmd_vel` を購読し、速度制限・加速度制限・指数平滑化・
   安全停止を適用して `/gazebo/cmd_vel` へ再発行します（処理は 10 ms 未満）。
3. **gazebo** が `/gazebo/cmd_vel` を Gazebo へブリッジしてロボットの差動駆動を
   動かし、`/odom`・`/imu`・`/clock` を ROS 2 へ返します。

## ディレクトリ構成

```
.
├── docker-compose.yml          # ros_net ブリッジ上の3コンテナ構成（マスターレス）
├── run_keyboard.sh             # 起動スクリプト (macOS / Linux / WSL)
├── run_keyboard.ps1            # 起動スクリプト (Windows)
├── test_integration.sh         # E2E 統合テスト
├── keyboard_input/             # keyboard_controller コンテナ
│   ├── Dockerfile
│   ├── requirements.txt
│   └── src/keyboard_input_controller.py
├── control_logic/              # control_logic コンテナ
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── src/control.py
│   └── tests/test_control.py   # ヘッドレス単体テスト（ROS 不要）
├── gazebo_simulator/           # gazebo コンテナ
│   ├── Dockerfile
│   ├── entrypoint.sh
│   ├── models/simple_robot/    # URDF + model.config
│   └── worlds/empty.world      # SDF ワールド
└── scenarios/                  # JSON デモシナリオ
    ├── demo_scenario_01.json   # "Simple Forward Motion"
    └── demo_scenario_02.json   # "Square Pattern"
```

## 前提条件

- **Docker**（Engine 20.10 以上）と **Docker Compose** v2（`docker compose`）。
  旧 v1（`docker-compose`）も可。起動スクリプトが自動判別します。
- 動作環境のいずれか: **macOS**（Docker Desktop）、Windows の **WSL2**
  （Docker Desktop の WSL2 バックエンド）、**Ubuntu/Linux**（Docker Engine）。
- Web ブラウザ（`localhost:8080` でシミュレータを表示するため）。
- ローカルへの Python/ROS インストールは不要です（すべてコンテナ内で動作）。
  任意のヘッドレス単体テストのみ Python 3 が必要です。

## 導入手順

```bash
# 1. リポジトリを取得
git clone https://github.com/taku0310/gazebo-keyboard-control.git
cd gazebo-keyboard-control

# 2. コンテナイメージをビルド（初回のみ。Gazebo は時間がかかります）
docker compose build

# 3.（任意）compose ファイルの健全性チェックとヘッドレス単体テスト
docker compose config >/dev/null && echo "compose OK"
python3 -m unittest discover -s control_logic/tests
```

> `gazebo` イメージは大きめです（Gazebo Harmonic + ROS 2 Jazzy + ros_gz bridge
> + noVNC）。初回の `build` は数分かかり、以降はキャッシュされます。詳細は後述の
> **既知の注意点** を参照してください。

## 実施手順

起動スクリプトはバックエンドのサービス（`control_logic`・`gazebo`）を
デタッチ起動し、数秒待って DDS 探索が整ってから keyboard_controller を対話的に
起動します。`Ctrl+C` で全コンテナを停止・後片付けします。

### 1. キーボードによる手動操作

```bash
# macOS / Linux / WSL2
bash run_keyboard.sh
```
```powershell
# Windows
.\run_keyboard.ps1
```

下記のキーでロボットを操作し、ブラウザ **http://localhost:8080** で動きを確認します。

### 2. シナリオの自動再生

```bash
# シナリオを読み込み、SPACE キーで再生開始:
bash run_keyboard.sh --scenario scenarios/demo_scenario_01.json

# 起動時に自動再生し、終了後に終了（再現性デモ向け）:
bash run_keyboard.sh --scenario scenarios/demo_scenario_01.json --auto
```
```powershell
.\run_keyboard.ps1 -Scenario "scenarios/demo_scenario_01.json" -Auto
```

任意フラグ: `--log <file>` で出力をログファイルにも記録。`bash run_keyboard.sh --help` でヘルプ表示。

### 3. シミュレーションの表示

ブラウザで **http://localhost:8080** を開きます（noVNC が Gazebo GUI を配信）。

### 4. 停止

起動スクリプトのターミナルで `Ctrl+C` を押すと `docker compose down` が実行され、
全コンテナが停止します。手動で停止する場合は `docker compose down`。

### 5. テストの実行

```bash
# ヘッドレス単体テスト（Docker / ROS 不要）:
python3 -m unittest discover -s control_logic/tests

# フル統合テスト（Docker が必要）:
bash test_integration.sh            # --quick で長いシナリオをスキップ
```

### キーバインド

| キー | 動作 | キー | 動作 |
|-----|--------|-----|--------|
| `W` / `↑` | 前進 | `+` / `=` | 速度スケール アップ |
| `S` / `↓` | 後退 | `-` / `_` | 速度スケール ダウン |
| `A` / `←` | 左旋回 | `R` | 速度リセット |
| `D` / `→` | 右旋回 | `SPACE` | シナリオ再生 |
| | | `Q` / `ESC` | 終了 |

## シナリオ

`scenarios/` 内の JSON ファイルは、再現性のあるデモ用に `Twist` コマンドの
タイムラインを記述します:

```json
{
  "name": "Simple Forward Motion",
  "description": "...",
  "duration_seconds": 10,
  "commands": [
    {"timestamp": 0.0, "description": "前進",
     "linear": {"x": 1.0, "y": 0.0, "z": 0.0},
     "angular": {"x": 0.0, "y": 0.0, "z": 0.0}}
  ]
}
```

コマンドは `timestamp` 順に適用され、最後のコマンドの状態は `duration_seconds`
まで保持された後、ロボットは停止します。

## 安全制約（control_logic）

| 制約 | 既定値 |
|------------|---------|
| 最大並進速度 | 2.0 m/s（クリップ） |
| 最大角速度 | 2.0 rad/s（クリップ） |
| 最大加速度 | 1.0 m/s²（レート制限） |
| 最大角加速度 | 1.0 rad/s²（レート制限） |
| 平滑化 | 指数フィルタ, α = 0.3 |
| 緊急停止 | `/emergency_stop`（`std_msgs/Bool`）→ 強制停止 |
| 接触停止 | `/contact`（`std_msgs/Bool`）→ 強制停止（任意） |

すべて CLI フラグで調整可能です（`control_logic/src/README.md` 参照）。

## テスト

```bash
# 安全パイプラインのヘッドレス単体テスト（Docker / ROS 不要）:
python3 -m unittest discover -s control_logic/tests

# フル統合テスト（Docker が必要）:
bash test_integration.sh          # --quick で長いシナリオをスキップ
```

統合テストはコンテナ起動・ROS 通信・E2E 遅延（目標 100 ms 未満）・シナリオ再生を
ツリー形式のサマリーで検証します。

## ロボット

`simple_robot` は差動二輪ロボットです: 0.5×0.3×0.2 m のボックス車体（10 kg）、
0.05 m の駆動輪2輪（continuous ジョイント、5 rad/s 制限）、前方キャスター。
`gazebo_simulator/models/simple_robot/simple_robot.urdf` に慣性・摩擦・gz-sim
DiffDrive プラグイン・IMU センサとともに定義されています。

## 既知の注意点

本プロジェクトは **ROS 2 Jazzy** と **Gazebo Sim Harmonic** を併用します。これは
公式に第一級サポートされる組み合わせで、`ros_gz`（bridge / sim）が apt で素直に
導入できます:

- `gazebo` コンテナは ROS 2 Jazzy ベースに Gazebo Harmonic（`gz-harmonic`）と
  `ros-jazzy-ros-gz-bridge` を載せ、GUI を **noVNC** で配信します（Gazebo Sim には
  組み込みの Web UI が無いため）。レンダリング系センサは無いため
  `gz-sim-sensors-system` は読み込んでいません（IMU は `gz-sim-imu-system` で処理）。
- Gazebo Harmonic の既定物理エンジンは **DART** です。world では `max_step_size`
  と `real_time_*` のみ確実に反映されます（ODE 専用の solver/constraints 設定は
  DART では無効なので、混乱を避けるため world から削除しています）。
- ロボットは `z=0.5` でスポーンし、車輪上（約 0.13 m）に着地します。
- **キー入力**は既定で TTY が attach されていれば `stdin (termios)`、なければ
  pynput（表示が必要）の順で自動選択されます。コンテナ内では stdin モードが
  使われるため、ホストの表示バックエンドに依存しません。`--input stdin` /
  `--input pynput` で明示指定も可能です。
- ROS 2 はマスターレスで、ノードは同じ `ROS_DOMAIN_ID` のもと DDS で相互探索します。
  Docker ブリッジ越しのマルチキャストが不安定な環境（一部 Docker Desktop など）
  では、同梱の `docker-compose.discovery.yml` overlay で **Fast DDS Discovery
  Server**（ユニキャスト）に切り替えられます。
- `ROS_DOMAIN_ID` は環境変数で上書き可能（既定 42）。
  例: `ROS_DOMAIN_ID=99 docker compose up -d` で別スタックと分離できます。

Python ノード・シナリオ・安全パイプラインはヘッドレスで検証済みです。フルの
Docker/Gazebo ビルドとランタイムは、実際の Docker ホストでの検証が必要です。

## トラブルシューティング

システムが動作しない場合は [TROUBLESHOOT.md](TROUBLESHOOT.md) を参照してください。
起動時エラー・ROS 通信・Gazebo・キーボード入力・パフォーマンスの各カテゴリ別に、
確認コマンドと解決方法をまとめています。

## ライセンス

リポジトリを参照してください。
