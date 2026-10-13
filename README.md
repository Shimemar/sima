# sima

このリポジトリは、SiMa / Modalix 関連の実験・検証・デモ用コードをまとめたワークスペースです。  
主に DevKit 向けアプリ、PCIe Host/Card 向け検証コード、各種起動メモを管理しています。

## リポジトリの全体像

- `Devkit_0.4.0/` : DevKit 向けのサンプルアプリと実験コードを格納
- `PCIe_HHHL/` : PCIe を使った AI 推論やマルチストリーム解析の実験を格納
- `AIsonar/` と `Scouter/` : センサーデータ収集やストリーム監視の関連実験を格納

## 主要なフォルダ

### `Devkit_0.4.0/`
DevKit 上で動作する AI / 画像処理のサンプルをまとめた領域です。

- `high-density-multi-stream-object-detector/`
  - 複数の RTSP 映像をまとめて処理する物体検出の実験コード
- `multimodal-assistant_e5/`
  - マルチモーダル AI アシスタントの実装
- `taste_in_clothes/`
  - 服装や画像判定のデモ・検証コード
- `Yolo_panoptic/`
  - 室内シーンのパノプティックセグメンテーション実験
- `oled/`
  - OLED 制御の簡易テストコード
- `Readme.txt`
  - DevKit と HOSTPC の起動/実行手順メモ

### `PCIe_HHHL/`
PCIe Card / Host 関連の実験アプリと検証環境をまとめた領域です。

- `llm/`
  - PCIe カード上で動かすローカル LLM / 音声・画像対応アプリ
- `pcie-high-density-multi-stream-object-detector/`
  - 高密度マルチストリーム物体検出アプリ
- `README.md`
  - PCIe 関連の全体概要と入口

## AIsonar / Scouter について

- `AIsonar`
  - センサー入力や外部データを使った推定・検知の実験モジュール群
- `Scouter`
  - 映像やセンサーデータを監視し、前処理やログ出力を行うユーティリティ群

## このリポジトリの用途

- DevKit 向けのサンプル実行とテスト
- PCIe Host/Card での推論・検証
- マルチモーダル AI / 画像認識 / 物体検出の実験管理
- 実行手順や設定メモの残し場

## 補足

このリポジトリは実験用・検証用のワークスペースとして管理されています。  
そのため、各サブフォルダ内の README や設定ファイルを確認しながら利用するのが適切です。

![mViewer screenshot](PCIe_HHHL/mViewer/Screenshot%20from%202026-10-02%2021-37-51.png)
