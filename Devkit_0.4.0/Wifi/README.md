# WiFi 接続手順（TP-Link AX300 / AIC8800DC）

このディレクトリには、SiMa Modalix DevKit に TP-Link の USB 無線 LAN ドングル（AIC8800DC / TP-Link AX300）を取り付けて、Wi‑Fi 接続まで進めた際の記録と手順がまとめられています。

## 概要

対象機器:
- DevKit: SiMa Modalix DevKit（eLxr 12 / カーネル `6.18.3-modalix`）
- USB 無線 LAN: TP-Link AX300（AIC8800DC チップ, Wi‑Fi 6, 1x1）
- 接続先: `ParlmAir_AC`（WPA3 / 2.4GHz）

結果:
- Wi‑Fi 接続に成功した
- インターネット疎通を確認済み
- 通常は有線 `end0` が優先され、Wi‑Fi は補助接続として利用可能

## 主要な確認事項

### デバイス状態

ドングルは初期状態では `a69c:5721`（AICsemi Aic MSC, 「ドライバ CD」モード）として認識されます。Linux では `eject` を使ってストレージモードから Wi‑Fi モードへ切り替える必要があります。

その後、USB は次のように認識されます。

- `a69c:5721` → CD モード（Windows 用ドライバ CD）
- `3625:0110` → Wi‑Fi モード（AIC8800DC）

### 取り込み時の状況

導入前に以下が確認されていました。

- カーネルドライバ: 未導入
- ファームウェア: `/lib/firmware/aic8800DC/` に既存あり
- udev ルール: `/etc/udev/rules.d/aic.rules` は存在していたが `eject` 未インストールで機能していなかった
- カーネルヘッダ: 未インストール
- NetworkManager / `wpasupplicant`: 利用可能

## 必要な作業

### 1. 必要パッケージを入れる

```bash
sudo apt-get install -y linux-headers-6.18.3-modalix eject iw usb-modeswitch
ls -la /lib/modules/$(uname -r)/build
```

### 2. ドライバソースを取得してビルド

採用したドライバ:
- https://github.com/Kiborgik/aic8800dc-linux-patched

```bash
mkdir -p ~/src && cd ~/src
git clone --depth 1 https://github.com/Kiborgik/aic8800dc-linux-patched.git
cd aic8800dc-linux-patched/drivers/aic8800
make -j6 KDIR=/lib/modules/$(uname -r)/build
find . -name "*.ko"
```

ビルド後、以下のモジュールが生成されていれば良いです。

- `aic_load_fw.ko`
- `aic8800_fdrv.ko`

### 3. ファームウェアとモジュールをインストール

```bash
S=~/src/aic8800dc-linux-patched

sudo cp -a /lib/firmware/aic8800DC /lib/firmware/aic8800DC.bak-$(date +%Y%m%d)
sudo rm -rf /lib/firmware/aic8800DC
sudo cp -a $S/fw/aic8800DC /lib/firmware/aic8800DC

D=/lib/modules/$(uname -r)/kernel/drivers/net/wireless/aic8800
sudo mkdir -p $D
sudo cp $S/drivers/aic8800/aic_load_fw/aic_load_fw.ko \
        $S/drivers/aic8800/aic8800_fdrv/aic8800_fdrv.ko $D/
sudo depmod -a

sudo cp $S/tools/aic.rules /etc/udev/rules.d/aic.rules
sudo udevadm control --reload
```

### 4. ドングルを抜き差しして Wi‑Fi モードへ切り替え

物理的に抜き差しすると、udev ルールが自動で `eject` を実行し、ドングルが `3625:0110` として再認識されます。

確認コマンド:

```bash
lsusb | grep 3625
ip -br link
sudo dmesg | grep -iE "aic|rwnx|wlan" | tail
```

インターフェース名は `wlx<MACアドレス>` のように自動生成されます。今回の環境では以下の状態でした。

```text
3: wlxbc071de44cd6: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500
    inet 192.168.88.80/24
```

## Wi‑Fi 接続手順

NetworkManager を使って接続します。

```bash
sudo nmcli dev wifi rescan
nmcli -f SSID,CHAN,SIGNAL,SECURITY dev wifi list
sudo nmcli dev wifi connect "<SSID>" password "<パスワード>" ifname wlxbc071de44cd6
```

今回は接続先が `ParlmAir_AC`（WPA3 / 2.4GHz）で、成功しています。

## 動作確認

```bash
W=wlxbc071de44cd6
nmcli dev
ip -br addr show $W
/usr/sbin/iw dev $W link
ping -I $W -c3 8.8.8.8
ip route
```

接続確認の結果:

- SSID: `ParlmAir_AC`
- 電波: -45 dBm
- 通信速度: 143.3 Mbit/s（HE-MCS 11, 1ストリーム）
- IP: `192.168.88.80/24`
- ゲートウェイ: `192.168.88.254`
- `8.8.8.8` への ping 成功

## ルーティングの優先度

```text
default via 10.42.0.1      dev end0            metric 100
default via 192.168.88.254 dev wlxbc071de44cd6 metric 600
```

この構成では、有線 `end0` が通常優先です。したがって、NFS や `dk` などの内部通信は従来通り有線経路を利用し、Wi‑Fi は自動的にバックアップ経路として使われます。

Wi‑Fi を優先したい場合は以下を設定します。

```bash
sudo nmcli con modify ParlmAir_AC ipv4.route-metric 50
sudo nmcli con up ParlmAir_AC
```

## つまずいたポイントと対策

### `eject` がないため Wi‑Fi モードへ移行できない

- `/etc/udev/rules.d/aic.rules` は存在していたが、`eject` が未導入だった
- `sudo apt install eject` で解決

### 最初のドライバが不採用だった

- `radxa-pkg/aic8800` を試したが、`3625:0110` を正しく扱えず、初期化に失敗
- 最終的に `Kiborgik/aic8800dc-linux-patched` を採用

### 初期化に失敗するとドングルがハングする

- ソフトウェアでの復旧が難しく、物理的に抜き差しすることで回復可能
- ドライバの切り替え時には、古いモジュールをアンロードしてから抜き差しする必要がある

## 保守メモ

- カーネル更新時は、ドライバを再ビルドする必要がある
- `wlan` が出ない場合はまず `lsusb` を確認
  - `a69c:5721` のままなら CD モード
  - `3625:0110` なのに初期化失敗なら抜き差し

## 参考ファイル

- `Devkit_AX3000_wifi.md`: 実際の手順書
- `wifi_AX3000.txt`: 接続後の `ip a` / `lsusb` 確認結果

## まとめ

本手順により、SiMa DevKit で TP-Link AX300（AIC8800DC）を動作させ、NetworkManager 経由で Wi‑Fi 接続を確立する方法が確認できました。重要なのは、USB モード切替 (`eject`) と、TP-Link 向けの専用ドライバを使う点です。

---

この README は、`Devkit_AX3000_wifi.md` と `wifi_AX3000.txt` の内容を基に、手順の概要と実運用上のポイントを整理したものです。
