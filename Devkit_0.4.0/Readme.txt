This is HOSTPC(42.10.0.1) work space /home/shimizu/neat_2.1.2

start FNS&NEAT:
[HOST]
sdk start
sdk neat

[devkit]
sudo mount -t nfs 10.42.0.1:/home/shimizu/neat_2.1.2 /workspace/
source ~/pyneat/bin/activate

[llm recver]
bash /usr/bin/fix_devkit_runtime.sh


---------------------------------------
multi-llm (gemma4 , quen3 )

[devkit]
source ~/pyneat/bin/activate
cd /workspace/prebuilt-apps/examples/genai/
run.sh

[HOST]
cd multillm/
source .venv/bin/activate
python vlm_movie.py 


---------------------------------------
Detection-VLM:

source ~/pyneat/bin/activate

cd prebuilt-apps

[HOST]
dk examples/genai/detection-to-vlm-assistant/src/python/genai_server.py --config examples/genai/detection-to-vlm-assistant/src/common/config.yaml

dk examples/genai/detection-to-vlm-assistant/src/python/detector_app.py --config examples/genai/detection-to-vlm-assistant/src/common/config.yaml


[Devkit]
python3 examples/genai/detection-to-vlm-assistant/src/python/genai_server.py --config examples/genai/detection-to-vlm-assistant/src/common/config.yaml

python3 examples/genai/detection-to-vlm-assistant/src/python/detector_app.py --config examples/genai/detection-to-vlm-assistant/src/common/config.yaml


---------------------------------------
Detection-VLM:　ファッション

[VScode]
cd /workspace/taste_in_clothes
dk ./fashion_judge_server.py --port 8765

[HostPC]
cd /home/shimizu/neat_2.1.2/taste_in_clothes
source .venv/bin/activate
python3 hostpc_gui.py


ディスプレイバージョン
[HOSTPC]
[terminal1 docker]
cd /workspace/taste_in_clothes
dk ./fashion_judge_server.py --port 8765

[terminal2 docker]
cd /workspace/taste_in_clothes
dk ./devkit_direct_hdmi.py 

[devkit]

[terminal1 docker]
cd /workspace/taste_in_clothes
source ~/pyneat/bin/activate
python ./fashion_judge_server.py --port 8765

[terminal2 docker]
cd /workspace/taste_in_clothes
source ~/pyneat/bin/activate
python ./devkit_direct_hdmi.py 



---------------------------------------
Grounding-Dino:

[devkit]
cd /media/nvme/sima-ai-apps-grounding_dino
source .venv/bin/activate

[画像をtextで認識]
python examples/python/02_visualize.py /opt/grounding_dino_fast/assets /workspace/colorcar.jpg "a red car" /workspace/out.png
python examples/python/02_visualize.py /opt/grounding_dino_fast/assets /workspace/Gdino_s1.png "a dog.a red car. a bicycle. a semaphore" /workspace/out.png

python examples/python/02_visualize.py /opt/grounding_dino_fast/assets /workspace/bear.png "bear." /workspace/bear_out.png
python examples/python/02_visualize.py /opt/grounding_dino_fast/assets /workspace/boar.png "boar." /workspace/boar_out.png
python examples/python/02_visualize.py /opt/grounding_dino_fast/assets /workspace/Raccoon.png "Raccoon." /workspace/Raccoon_out.png



[画像１枚処理、zmq型]
python examples/python/03_zmq_server.py /opt/grounding_dino_fast/assets --bind tcp://10.42.0.76:5556

[リアルタイム動画に対してのgrounding Dino]
[Devkit]
sudo fuser -v /dev/m4_lp_mbox
sudo kill <PID>
python examples/python/05server_zmq.py /opt/grounding_dino_fast/assets --bind tcp://10.42.0.76:5556

[hostPc]
python m_dino.py 0 "a person." --server tcp://10.42.0.76:5556
python t_dino.py 0 --server tcp://10.42.0.76:5556 --frame-skip 4 --text "a face."

python t_dino.py 4 --server tcp://10.42.0.76:5556 --frame-skip 4 --text "a caps."

*** mla busy が出る場合は、表示されたタスクをkillしてください。
sudo fuser -v /dev/m4_lp_mbox
sudo kill <PID>



---------------------------------------
neat作成のLLMデモ（キックワード: hi sima）
マイク、スピーカーをdevkitに接続して、動作させる事

cd ./llm_ngen_codex

python 13_voice_conversation_openjtalk.py \
  --asr-model /media/nvme/llima/models/whisper-small-a16w8 \
  --llm-model /media/nvme/llima/models/gemma4-E4B-it 
  --wake-model ./vosk-wake-model \
  --openjtalk-dic \
    /var/lib/mecab/dic/open-jtalk/naist-jdic \
  --openjtalk-voice \
    /usr/share/hts-voice/nitech-jp-atr503-m001/nitech-jp-atr503-m001.htsvoice 

./build-cpp/13_voice_conversation_openjtalk \
  --asr-model /media/nvme/llima/models/whisper-small-a16w8 \
  --llm-model /media/nvme/llima/models/gemma4-E4B-it \
  --wake-model ./vosk-wake-model \
  --openjtalk-dic /var/lib/mecab/dic/open-jtalk/naist-jdic \
  --openjtalk-voice /usr/share/hts-voice/nitech-jp-atr503-m001/nitech-jp-atr503-m001.htsvoice


neat作成のLLMデモ（キックワード: hi sima）
カメラをdevkitに接続させて起動すること。立ち上がるまで、90秒
devkitは、起動前に下記のスクリプト実行

bash /usr/bin/fix_devkit_runtime.sh


cd ./vlm_ngen_demo
source .venv/bin/activate

--- devkit --- 
dk ./main.py --config ./config/default.conf 

--- HostPC ---
python3 viewer.py


---------------------------------------
Multi-Stream Object Detector

[host]
cd prebuilt-apps
APP_DIR=examples/object-detection/high-density-multi-stream-object-detector
