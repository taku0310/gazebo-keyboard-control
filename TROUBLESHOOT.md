# トラブルシューティングガイド

システムが期待どおり動作しない場合の診断・解決手順をまとめています。
まず下の「クイック診断フロー」で大まかな切り分けを行い、該当するセクションへ
進んでください。

関連ドキュメント: [README.md](README.md) / [統合テスト](test_integration.sh)

---

## クイック診断フロー

```
症状は？
│
├─ コンテナが起動しない / build できない ───────▶ 1. 起動時エラー
│
├─ 起動したがロボットが動かない
│   │
│   ├─ ros2 topic でトピックが見えない ──────────▶ 2. ROS 通信エラー
│   ├─ localhost:8080 が開けない ───────────────▶ 3. Gazebo エラー
│   └─ キー入力が効かない ───────────────────────▶ 4. キーボード入力エラー
│
└─ 動くが遅い / 重い / カクつく ─────────────────▶ 5. パフォーマンス問題
```

まず全体状態を確認する基本コマンド:

```bash
docker compose ps                 # 各コンテナの状態
docker compose logs --tail 50     # 直近のログ（全サービス）
docker compose logs control_logic # 特定サービスのログ
```

> ROS 2 はマスターレスです（roscore コンテナはありません）。各ノードは同じ
> `ROS_DOMAIN_ID`（本リポジトリでは 42）のもと DDS で相互探索します。

---

## 1. 起動時エラー

### 1-1. `docker-compose: command not found` / `docker: command not found`

**原因:** Docker がインストールされていない、または新しい Compose v2
（`docker compose`、ハイフンなし）のみが入っている。

**確認:**
```bash
docker version
docker compose version      # v2（推奨）
docker-compose version      # v1（旧）
```

**解決:**
- Docker 未導入 → [Docker Desktop](https://www.docker.com/products/docker-desktop/)
  （macOS/Windows）または Docker Engine（Linux）を導入。
- `docker-compose`（v1）が無い場合は `docker compose`（v2）を使用してください。
  本リポジトリの `run_keyboard.sh` / `.ps1` は **両者を自動判別**するので、
  通常はスクリプト経由での起動で問題ありません。
- Linux で `docker` が `sudo` 必須の場合は 1-3 を参照。

### 1-2. `Port is already allocated` / `Port 8080 already in use`

**原因:** Gazebo Web UI の 8080 が、別の compose・他プロセスに使われている。
（ROS 2 はマスターレスのため固定ポート 11311 は使いません。DDS は動的ポートを使用。）

**確認:**
```bash
# ポートを使用しているプロセスを特定
lsof -i :8080           # macOS / Linux
# あるいは
sudo ss -ltnp | grep 8080   # Linux
# 残存コンテナの確認
docker ps -a
```

**解決:**
```bash
# 1) 本プロジェクトの残存コンテナを片付ける
docker compose down --remove-orphans

# 2) それでも衝突する場合はホスト側ポートを変更（docker-compose.yml）
#    例: "8081:8080" のように左側（ホスト）を変える
```
ホスト側ポートを変えた場合、Web UI は変更後のポート（例 `localhost:8081`）で開きます。

> DDS 探索が同一ホストの別プロジェクトと干渉する場合は、`ROS_DOMAIN_ID` を
> 変更して分離してください（compose の各サービスの環境変数）。

### 1-3. `Permission denied`（Docker ソケット / ファイル）

**原因 A（Docker ソケット）:** 現在のユーザーが `docker` グループに属していない（Linux）。

```bash
# 症状例: Got permission denied while trying to connect to the Docker daemon socket
sudo usermod -aG docker "$USER"   # グループに追加
# 反映のため一度ログアウト→ログイン（または newgrp docker）
newgrp docker
docker ps                          # sudo なしで動けば OK
```

**原因 B（スクリプト実行権限）:**
```bash
chmod +x run_keyboard.sh test_integration.sh
# もしくは bash 経由で実行
bash run_keyboard.sh
```

**原因 C（Windows / PowerShell の実行ポリシー）:**
```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\run_keyboard.ps1
```

### 1-4. ビルドが失敗する（特に `gazebo`）

**原因:** `gazebo` イメージは ROS 2 Jazzy + Gazebo Harmonic（`gz-harmonic`）+
`ros-jazzy-ros-gz-bridge` + noVNC と重く、apt リポジトリ（OSRF/ROS）への到達性や
ディスク容量で失敗することがあります（[README の既知の注意点](README.md#既知の注意点)）。

**確認・解決:**
```bash
# 詳細ログ付きで該当サービスのみビルド
docker compose build --no-cache --progress=plain gazebo
```
- OSRF の apt 鍵/リポジトリ追加に失敗する場合はネットワーク/プロキシを確認。
- まずは gazebo を除く 2 コンテナ（`keyboard_controller` `control_logic`）だけで
  疎通確認するのも有効です:
  ```bash
  docker compose up -d control_logic
  ```

---

## 2. ROS 通信エラー

通信経路: `keyboard_controller → /cmd_vel → control_logic → /gazebo/cmd_vel → gazebo`

```
[keyboard] ──/cmd_vel──▶ [control_logic] ──/gazebo/cmd_vel──▶ [gazebo(bridge)]
     │                          │                                   │
     └──────── ROS 2 DDS discovery (masterless, ROS_DOMAIN_ID=42) ──┘
```

### 2-1. ノードが互いを見つけられない（トピックが空）

**原因:** ROS 2 はマスターレスで DDS により探索します。`ROS_DOMAIN_ID` が
サービス間で不一致、または同一ネットワークに居ない、DDS マルチキャストが
通っていない、などが原因。

**確認:**
```bash
# 各サービスの ROS_DOMAIN_ID が一致しているか（本リポジトリでは 42）
docker compose exec control_logic printenv ROS_DOMAIN_ID
docker compose exec gazebo printenv ROS_DOMAIN_ID

# control_logic からトピック/ノードが見えるか
docker compose exec control_logic bash -lc \
  "source /opt/ros/jazzy/setup.bash && ros2 topic list && ros2 node list"
```

**解決:**
- `docker-compose.yml` の全サービスに同じ `ROS_DOMAIN_ID=42` があるか確認。
- 全サービスが同じ `ros_net` に接続しているか確認（2-3 参照）。
- それでも探索しない場合、DDS のマルチキャスト不通が疑わしいので、Fast DDS の
  discovery 設定（例: ピアの明示指定や discovery server）を検討。まずは
  `docker compose down && docker compose up -d` で作り直す。

### 2-2. `Cannot publish to /cmd_vel` / トピックが見えない

**確認（control_logic コンテナを ROS 2 CLI として利用）:**
```bash
RC="docker compose exec -T control_logic bash -lc"

# 1) トピック一覧に /cmd_vel と /gazebo/cmd_vel があるか
$RC "source /opt/ros/jazzy/setup.bash && ros2 topic list"

# 2) パブリッシャ／サブスクライバ数を確認
$RC "source /opt/ros/jazzy/setup.bash && ros2 topic info /cmd_vel"

# 3) 実際に値が流れているか
$RC "source /opt/ros/jazzy/setup.bash && timeout 3 ros2 topic echo /cmd_vel"

# 4) 手動でテスト発行 → control_logic が /gazebo/cmd_vel に再発行するか
$RC "source /opt/ros/jazzy/setup.bash && \
  timeout 3 ros2 topic pub -r10 /cmd_vel geometry_msgs/msg/Twist \
  '{linear: {x: 1.0}, angular: {z: 0.5}}'"
$RC "source /opt/ros/jazzy/setup.bash && ros2 topic echo --once /gazebo/cmd_vel"
```

**よくある原因と対策:**
- **control_logic が起動していない** → `docker compose up -d control_logic`。
- **トピック名の不一致**: 出力は必ず `/gazebo/cmd_vel`。`/cmd_vel` のまま
  Gazebo へ繋ぐとロボットは動きません（設計上の規約）。
- **キーボード入力が無い**: 手動操作中はキーを押し続けないと速度は 0 のまま。
  検証目的なら上記 4) の `ros2 topic pub` か、シナリオ再生（`--auto`）を使用。

### 2-3. コンテナ間で探索/到達できない

**原因:** 各サービスが同じ Docker ネットワーク `ros_net` に接続していない、
またはネットワークが壊れている。

**確認:**
```bash
docker network inspect gazebo-keyboard-control_ros_net
# 3 つのコンテナが Containers に並んでいるか

# control_logic から gazebo へ到達できるか
docker compose exec control_logic getent hosts gazebo
```

**解決:**
```bash
# ネットワークを作り直す
docker compose down
docker compose up -d
```
それでも解決しない場合はネットワークを明示削除して再生成:
```bash
docker compose down
docker network rm gazebo-keyboard-control_ros_net 2>/dev/null || true
docker compose up -d
```

---

## 3. Gazebo エラー

### 3-1. `Web UI not accessible`（localhost:8080 が開けない）

Web UI は **noVNC**（Xvfb + x11vnc + websockify）で Gazebo GUI を配信します。

```
ブラウザ :8080 ──▶ websockify/noVNC ──▶ x11vnc :5900 ──▶ Xvfb(:1) ◀── gz sim
```

**確認:**
```bash
docker compose ps gazebo                       # 起動しているか
docker compose logs gazebo | tail -40          # Xvfb/x11vnc/novnc/gz のログ
curl -I http://localhost:8080                  # ホストから到達できるか
docker compose exec gazebo ss -ltnp | grep -E '8080|5900'   # コンテナ内のリッスン
```

**解決:**
- `gazebo` が `Exited` → ログを確認。`gz sim` の起動失敗や Xvfb 未起動が多い。
- ポート競合 → 1-2 を参照（`8080` を変更）。
- `gazebo` がそもそもビルド/起動できない場合は 1-4 を参照。コンテナが落ちていても
  キーボード→control_logic の経路は `ros2 topic echo /gazebo/cmd_vel` で確認できます。

### 3-2. `Cannot load URDF` / ロボットが表示されない

**原因:** `model://simple_robot` が解決できない（リソースパス未設定 / model.config
の不整合 / URDF 文法エラー）。

**確認:**
```bash
# リソースパスにモデル/ワールドが含まれているか
docker compose exec gazebo printenv GZ_SIM_RESOURCE_PATH
# 期待値: /root/.gazebo/models:/root/.gazebo/worlds

# モデルファイルがマウントされているか
docker compose exec gazebo ls /root/.gazebo/models/simple_robot
# model.config と simple_robot.urdf が見えること

# URDF の XML 整形式チェック（ホスト側）
python3 -c "import xml.dom.minidom as m; m.parse('gazebo_simulator/models/simple_robot/simple_robot.urdf'); print('URDF OK')"
```

**解決:**
- `model.config` の `<sdf>simple_robot.urdf</sdf>` がファイル名と一致しているか確認。
- `docker-compose.yml` の volume マウント
  （`./gazebo_simulator/models:/root/.gazebo/models`）が正しいか確認。
- 慣性・ジョイント等の妥当性は ros-robotics 観点のレビュー対象。XML が壊れている
  場合は上記チェックでエラー箇所が分かります。

### 3-3. `Physics simulation too slow`（RTF が 1.0 を大きく下回る）

**原因:** ソフトウェアレンダリング（GPU 無し）、時間ステップが細かすぎる、
ホスト性能不足。

**確認:** Gazebo の Real Time Factor（RTF）を画面または `/clock` のレートで確認。

**最適化:**
- `gazebo_simulator/worlds/empty.world` の物理設定を調整:
  ```xml
  <max_step_size>0.001</max_step_size>          <!-- 0.002〜0.004 に上げると負荷減 -->
  <real_time_update_rate>1000</real_time_update_rate>
  ```
- 可視化が不要なら GUI（noVNC）を止め、`gz sim -s`（サーバのみ）で実行すると
  大幅に軽くなります（`gazebo_simulator/entrypoint.sh` を調整）。
- Docker Desktop の割当（CPU/メモリ）を増やす。M1 Mac で amd64
  エミュレーション時は特に遅くなるため、ネイティブ arm64 イメージを優先。

---

## 4. キーボード入力エラー

### 4-1. ゲームパッド（PS4 コントローラ等）が認識されない

**仕様:** 本プロジェクトは **キーボードのみ**対応（`pynput` 使用）で、ゲームパッドは
**サポート対象外**です。`PS4 controller not found` のようなエラーは想定動作です。

**参考（拡張する場合）:** ゲームパッド対応を足すなら、別ノードで ROS の
`joy`（`sensor_msgs/Joy`）を読み、`/cmd_vel` に変換する `joy → Twist` ノードを
追加するのが定石です。本体の `control_logic` 以降はそのまま再利用できます。

### 4-2. `No input received`（キーを押しても動かない）

**原因:** キーボードキャプチャには tty/stdin と表示バックエンドが必要で、
コンテナ環境では制約があります。

**確認・対策:**
- **起動スクリプトを使う**: `run_keyboard.sh` / `.ps1` は keyboard_controller を
  `docker compose run`（対話 tty 付き）で起動します。`docker compose up -d` だけでは
  キー入力は効きません（ヘッドレスで即終了するのが既知挙動）。
- **macOS**: `pynput` は **アクセシビリティ権限**が必要な場合があります
  （システム設定 → プライバシーとセキュリティ → アクセシビリティ）。
- **Linux**: X サーバ／`python3-xlib` が必要。コンテナの設計上、表示が無いと
  リスナーは起動しません。動作確認だけなら**シナリオ自動再生**が確実:
  ```bash
  bash run_keyboard.sh --scenario scenarios/demo_scenario_01.json --auto
  ```
- **入力はしているが速度が 0**: 反対キーの同時押し（W+S など）は相殺されます。
  `R` でリセット、`📊 linear.x=… angular.z=…` のデバッグ表示で状態を確認。

### 4-3. `Scenario file not found`

**原因:** ホストのパスとコンテナ内のマウント先（`/app/scenarios`）の取り違え。

**確認:**
```bash
ls scenarios/                                   # ホスト側に存在するか
docker compose run --rm keyboard_controller ls /app/scenarios   # コンテナ内
```

**解決:**
- 起動スクリプトはホストパスの **ファイル名（basename）** を
  `/app/scenarios/<ファイル名>` に変換して渡します。よって渡すのは
  `scenarios/demo_scenario_01.json` のようなリポジトリ相対パスでOK:
  ```bash
  bash run_keyboard.sh --scenario scenarios/demo_scenario_01.json --auto
  ```
- 直接コンテナ内で実行する場合はコンテナパスを指定:
  ```bash
  docker compose run --rm keyboard_controller \
    python3 /app/src/keyboard_input_controller.py \
    --scenario /app/scenarios/demo_scenario_01.json --auto
  ```
- JSON 構文エラーの確認:
  ```bash
  python3 -c "import json; json.load(open('scenarios/demo_scenario_01.json')); print('JSON OK')"
  ```

---

## 5. パフォーマンス問題

### 5-1. `Low FPS`（Web UI がカクつく）

**原因:** noVNC は画面を VNC 経由でストリーミングするため、解像度・帯域・
レンダリング負荷の影響を受けます。

**最適化:**
- Xvfb の解像度を下げる（`gazebo_simulator/entrypoint.sh`）:
  ```bash
  Xvfb "${DISPLAY_NUM}" -screen 0 1280x720x24 &   # 例: 1024x576x24 に下げる
  ```
- ブラウザの noVNC 設定で画質（圧縮レベル）を調整。
- 3-3 の物理最適化（GUI 停止 / step size 調整）も FPS 改善に有効。

### 5-2. `High latency`（操作の反応が遅い / E2E 遅延が大きい）

目標は **E2E 100 ms 未満**。経路は「発行 20 Hz(50ms) + control_logic 処理(<10ms)
+ ネットワーク + Gazebo 反映」。

**計測:**
```bash
# 統合テストのレイテンシ計測（/cmd_vel → /gazebo/cmd_vel）
bash test_integration.sh --quick     # "Test 3: Latency Measurement" を参照

# 発行レートの確認（約 20 Hz であること）
docker compose exec -T control_logic bash -lc \
  "source /opt/ros/jazzy/setup.bash && timeout 3 ros2 topic hz /cmd_vel"
```

**チューニング:**
- `control_logic` の処理が 10 ms を超える場合は警告ログ（スロットル表示）が出ます。
  ホットパスでの過剰なログ・ブロッキング I/O が無いか確認。
- 発行周期は `keyboard_input_controller.py` の `PUBLISH_RATE_HZ`、制御周期は
  `control.py` の `--rate`（既定 20 Hz）。上げると遅延は減るが負荷は増。
- 平滑化フィルタ（`--alpha`、既定 0.3）を上げると応答は速く、下げると滑らかに。

### 5-3. `High memory usage`

**確認:**
```bash
docker stats --no-stream    # コンテナ別の CPU/メモリ
```

**対策:**
- 最も重いのは `gazebo`（Gazebo Harmonic + ROS 2 + noVNC）。可視化不要時は GUI/noVNC を
  止める（3-3 参照）か、`gazebo` を起動しない構成で開発:
  ```bash
  docker compose up -d control_logic
  ```
- Docker Desktop のメモリ割当を見直す（少なすぎると OOM、過剰だとホストを圧迫）。
- 使い終わったら確実に片付ける:
  ```bash
  docker compose down --remove-orphans
  docker system prune          # 未使用イメージ/キャッシュの削除（注意して実行）
  ```

---

## それでも解決しない場合

1. 全ログを取得して状況を保存:
   ```bash
   docker compose logs > debug_$(date +%Y%m%d_%H%M%S).log
   ```
2. クリーンに再起動:
   ```bash
   docker compose down --remove-orphans
   docker compose up -d --build
   ```
3. 静的検証・ヘッドレステストで構成要素を切り分け:
   ```bash
   python3 -m unittest discover -s control_logic/tests
   docker compose config >/dev/null && echo "compose OK"
   ```
4. 統合テストで全体を診断:
   ```bash
   bash test_integration.sh
   ```
