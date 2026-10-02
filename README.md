# sima

![mViewer screenshot](PCIe_HHHL/mViewer/Screenshot%20from%202026-10-02%2021-37-51.png)

このリポジトリは、SiMa / Modalix 関連の実験、検証、デモ、PCIe/DevKit 周りの開発成果物をまとめたワークスペースです。
主に、DevKit 向けアプリケーション、PCIe Host 向け検証コード、環境ごとの動作メモを格納しています。

## 主要なフォルダ

### `Devkit_0.4.0/`
DevKit 向けのサンプルアプリケーションや実験コードをまとめたフォルダです。
主に画像認識、マルチモーダル AI、物体検出、OLED 制御などの検証用コードが含まれています。

- `high-density-multi-stream-object-detector/`
  - 複数の RTSP 映像ストリームをまとめて処理する物体検出の実験コード
- `multimodal-assistant_e5/`
  - マルチモーダル AI アシスタント関連の実装
- `taste_in_clothes/`
  - ファッション判定や画像処理のデモ・実験コード
- `Yolo_panoptic/`
  - パノプティックセグメンテーションの実験・検証コード
- `oled/`
  - OLED 制御の簡易テストコード
- `Readme.txt`
  - DevKit / HOSTPC の起動手順や実行コマンドのメモ

### `PCIe_HHHL/`
PCIe Host / Card 向けの検証環境とアプリケーションをまとめたフォルダです。
ローカル LLM や高密度マルチストリーム物体検出など、PCIe を使った実験を管理しています。

- `llm/`
  - PCIe カード上で動かすローカル AI アプリや音声・画像対応のサーバー
- `pcie-high-density-multi-stream-object-detector/`
  - PCIe を使った複数ストリームの高速物体検出アプリケーション
- `README.md`
  - フォルダ全体の概要と使い方の入口

### `README_PCIe.md`
PCIe 関連の手順書や検証メモです。
インストール状況、デバイス確認、モデル取得、チュートリアル実行、検証結果などが記録されています。

## このリポジトリの用途

- DevKit 向けのサンプルやデモ実行コードの保存
- PCIe Host の検証・接続確認・推論実行の記録
- 実験ノートや動作手順の残し場
- 画像認識、音声、LLM、マルチモーダル処理のコード管理

## 参考情報

- `README_PCIe.md` は PCIe / ホスト側の詳細な作業記録です。
- `Devkit_0.4.0/Readme.txt` は DevKit / HOSTPC の起動・実行手順メモです。
- 各サブフォルダには、アプリ固有の README や設定ファイルが存在します。

## 補足

このリポジトリは実験用・検証用のワークスペースとして管理されており、実行環境やモデル配置パス、ホスト情報がファイル内に記載されていることがあります。
そのため、各ディレクトリの README と設定ファイルを参照しながら利用するのが適切です。
