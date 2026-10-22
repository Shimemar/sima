# DevKit に TP-Link USB 無線LAN（AIC8800DC）を導入する手順

SiMa Modalix DevKit（eLxr 12 / カーネル `6.18.3-modalix`）に TP-Link の USB 無線LANドングルを接続し、
Wi-Fi でネットワークに接続するまでの作業記録と再現手順。

- 作業日: 2026-10-22
- 対象ボード: `sima@10.42.0.76`
- 結果: `ParlmAir_AC`（WPA3, 2.4GHz）に接続成功。Wi-Fi 経由でインターネット疎通を確認済み

> 注: 製品名は lsusb / ドライバの対応表上 **TP-Link AX300**（AIC8800DC チップ, Wi-Fi 6, 1x1）。

---

## 1. デバイスの概要

| 状態 | USB ID | 説明 |
|---|---|---|
| 挿した直後 | `a69c:5721 aicsemi Aic MSC` | 「ドライバCD」モード（USBストレージ）。中身は Windows 用 `SetupInstall.exe` のみ |
| 切替後 | `3625:0110 AICSemi AIC8800DC` | Wi-Fi モード。インターフェース数 1（Wi-Fi のみ） |

Linux ではストレージを `eject` すると Wi-Fi モードに切り替わる。
AIC8800 系のドライバはメインラインのカーネルに含まれないため、外部ドライバ（out-of-tree）をビルドする必要がある。

## 2. 導入前の状態（調査結果）

| 項目 | 状態 |
|---|---|
| カーネルドライバ | なし |
| ファームウェア | `/lib/firmware/aic8800DC/` に存在（dpkg 管理外。過去の作業の残り） |
| udev ルール | `/etc/udev/rules.d/aic.rules` あり。ただし `eject` が未インストールのため動作していなかった |
| カーネルヘッダ | 未インストール（apt で `linux-headers-6.18.3-modalix` を入手可能） |
| ビルド環境 | `build-essential` あり |
| `cfg80211` / `mac80211` | モジュールあり |
| ネットワーク管理 | NetworkManager（`nmcli`）と `wpasupplicant` あり |

---

## 3. 導入手順

以下はすべて DevKit 上で実行する（`ssh sima@10.42.0.76`）。インターネットへは有線 `end0` 経由で接続できる前提。

### 3.1 必要なパッケージのインストール

```bash
sudo apt-get install -y linux-headers-6.18.3-modalix eject iw usb-modeswitch
ls -la /lib/modules/$(uname -r)/build   # ヘッダへのリンクがあることを確認
```

### 3.2 ドライバのソース取得とビルド

**TP-Link の `3625:0110` を公式にサポートしている** [Kiborgik/aic8800dc-linux-patched](https://github.com/Kiborgik/aic8800dc-linux-patched)
を使う（使用したバージョンは `6.4.3.0-patched.17`、コミット `7745043`）。

```bash
mkdir -p ~/src && cd ~/src
git clone --depth 1 https://github.com/Kiborgik/aic8800dc-linux-patched.git
cd aic8800dc-linux-patched/drivers/aic8800
make -j6 KDIR=/lib/modules/$(uname -r)/build      # DevKit 上でのビルドは約4分
find . -name "*.ko"    # aic_load_fw.ko と aic8800_fdrv.ko ができていればよい
```

> 同梱の `install.sh` は使わず、手動でインストールした（DKMS 未使用）。

### 3.3 ファームウェア・モジュール・udev ルールのインストール

```bash
S=~/src/aic8800dc-linux-patched

# ファームウェア（既存のものはバックアップしてから入れ替える）
sudo cp -a /lib/firmware/aic8800DC /lib/firmware/aic8800DC.bak-$(date +%Y%m%d)
sudo rm -rf /lib/firmware/aic8800DC
sudo cp -a $S/fw/aic8800DC /lib/firmware/aic8800DC

# カーネルモジュール
D=/lib/modules/$(uname -r)/kernel/drivers/net/wireless/aic8800
sudo mkdir -p $D
sudo cp $S/drivers/aic8800/aic_load_fw/aic_load_fw.ko \
        $S/drivers/aic8800/aic8800_fdrv/aic8800_fdrv.ko $D/
sudo depmod -a

# CD モードから自動で切り替える udev ルール（eject を使う）
sudo cp $S/tools/aic.rules /etc/udev/rules.d/aic.rules
sudo udevadm control --reload
```

### 3.4 ドングルを挿し直す

**ドングルを物理的に抜き差しする。** 次の順で自動的に処理される。

1. `a69c:5721`（CD モード）として認識される
2. udev ルールが `eject` を実行する
3. `3625:0110` として再認識される
4. `aic8800_fdrv` が自動でロードされる

確認方法:

```bash
lsusb | grep 3625          # 3625:0110 AICSemi AIC8800DC
ip -br link                # wlx<MACアドレス> のインターフェースが出ていること
sudo dmesg | grep -iE "aic|rwnx|wlan" | tail
```

インターフェース名は `wlan0` から自動で改名され、今回は `wlxbc071de44cd6` になった。

### 3.5 Wi-Fi への接続（NetworkManager）

```bash
sudo nmcli dev wifi rescan
nmcli -f SSID,CHAN,SIGNAL,SECURITY dev wifi list
sudo nmcli dev wifi connect "<SSID>" password "<パスワード>" ifname wlxbc071de44cd6
```

接続設定は NetworkManager に保存され、自動接続が有効になる（再起動・抜き差し後も自動で再接続）。

### 3.6 動作確認

```bash
W=wlxbc071de44cd6
nmcli dev
ip -br addr show $W
/usr/sbin/iw dev $W link          # iw は /usr/sbin にあり、一般ユーザーの PATH に入っていない
ping -I $W -c3 8.8.8.8
ip route
```

今回の結果:

| 項目 | 値 |
|---|---|
| SSID | `ParlmAir_AC`（WPA3, ch1 / 2412 MHz） |
| 電波の強さ | -45 dBm |
| 送信速度 | 143.3 Mbit/s（HE-MCS 11, 1ストリーム） |
| IP アドレス | `192.168.88.80/24`（DHCP、ゲートウェイ `192.168.88.254`） |
| 疎通確認 | `8.8.8.8` への ping 成功（応答 約35ms） |

### 3.7 経路の優先度

```
default via 10.42.0.1      dev end0            metric 100
default via 192.168.88.254 dev wlxbc071de44cd6 metric 600
```

- **ふだんは有線 `end0` が優先される。** そのため、ホストとの NFS や `dk` の通信は従来どおり有線を通る。
- **有線を抜けば、自動で Wi-Fi に切り替わる。**
- Wi-Fi を優先にしたい場合は次のようにする。

  ```bash
  sudo nmcli con modify ParlmAir_AC ipv4.route-metric 50
  sudo nmcli con up ParlmAir_AC
  ```

---

## 4. つまずいた点と経緯

### 4.1 `eject` がなく、Wi-Fi モードに切り替わらなかった

既存の udev ルールは `/usr/bin/eject` を呼ぶが、`eject` パッケージが未インストールだった。
`apt install eject` で解決した。

### 4.2 radxa-pkg/aic8800 では初期化に失敗した（不採用）

最初は [radxa-pkg/aic8800](https://github.com/radxa-pkg/aic8800) を使った。debian/patches を当て、USB 版をビルドした。
しかし、このドライバの対応表には `3625:0110` がない。そこで、ID を手作業で追加し、AIC8800DC として扱わせた。

- ドライバは `Number of interfaces: 1` を理由に、チップを AIC8800DW として扱った
- ファームウェアの書き込み後に `err_lmac_reqs` → `aicwf_rwnx_usb_platform_init err -1` で初期化に失敗した
- **TP-Link 専用の調整ファイル（`aic_userconfig_8800dc_3625.txt`）を使う処理などが入っていない**ため、ID を追加するだけでは動かないと判断した

### 4.3 初期化に失敗するとドングルが固まる

初期化に失敗したドングルは、ソフトウェアからは復旧できなかった。

- 試した方法: `/sys/bus/usb/devices/1-3.2/authorized` の 0→1 切替、ハブのポートの `disable` 切替
- 結果: `device descriptor read/64, error -110` や `device not accepting address` が出て、デバイスとして認識されなくなった
- **復旧方法は、物理的に抜き差しすることだけ**

また、古いドライバがロードされたまま抜き差しすると、古いドライバが先にドングルを掴んで初期化に失敗する。
このあと新しいドライバをロードしても `rd fail: -110` / `bin upload fail` になる。
**ドライバを入れ替えたら、古いドライバをアンロードしてから抜き差しする。**

### 4.4 解決: Kiborgik/aic8800dc-linux-patched

このドライバは `3625:0110` を TP-Link AX300 として正式にサポートしている。
TP-Link 用の調整ファイルを使う処理と、カーネル 6.18 対応の修正も入っている。これに切り替えて抜き差しすると、問題なく `wlan0` が作成された。

---

## 5. 保守上の注意

- **カーネルを更新したら、ドライバのビルドをやり直す。** DKMS を使っていないため、自動では再ビルドされない。

  ```bash
  sudo apt-get install -y linux-headers-$(uname -r)
  cd ~/src/aic8800dc-linux-patched/drivers/aic8800
  make clean && make -j6 KDIR=/lib/modules/$(uname -r)/build
  D=/lib/modules/$(uname -r)/kernel/drivers/net/wireless/aic8800
  sudo mkdir -p $D && sudo cp aic_load_fw/aic_load_fw.ko aic8800_fdrv/aic8800_fdrv.ko $D/
  sudo depmod -a
  ```

- **`wlan` が出ないときは、まず `lsusb` で状態を確認する。**
  - `a69c:5721` のままなら CD モードから切り替わっていない。`sudo eject /dev/sdX` を実行するか、`sudo usb_modeswitch -v a69c -p 5721 -KQ` を実行する
  - `3625:0110` なのに初期化に失敗している場合は、ドングルを抜き差しする

- **バックアップの場所**
  - `/lib/firmware/aic8800DC.bak-20261022`: 導入前からあったファームウェア
  - `/lib/firmware/aic8800DC.radxa-20261022`: radxa 版を試したときのファームウェア（不要なら削除してよい）
  - `~/src/aic8800`: radxa 版のソース（不採用。不要なら削除してよい）

- **アンインストール手順**

  ```bash
  sudo nmcli con delete ParlmAir_AC
  sudo modprobe -r aic8800_fdrv aic_load_fw
  sudo rm -rf /lib/modules/$(uname -r)/kernel/drivers/net/wireless/aic8800
  sudo depmod -a
  sudo rm /etc/udev/rules.d/aic.rules && sudo udevadm control --reload
  ```

## 参考

- [Kiborgik/aic8800dc-linux-patched](https://github.com/Kiborgik/aic8800dc-linux-patched): 採用したドライバ
- [radxa-pkg/aic8800](https://github.com/radxa-pkg/aic8800): 最初に試したドライバ（不採用）
