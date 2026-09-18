'use strict';
const $ = id => document.getElementById(id);
let cameraStream = null, attachment = null, history = [], busy = false, recording = null, recordStarting = false;
let shuttingDown = false;
let requests = new AbortController();

let speechController = null, speechUrl = null, speechVersion = 0;
function stopSpeech() {
  speechVersion++; speechController?.abort(); speechController = null;
  const audio = $('speechAudio'); audio.pause(); audio.removeAttribute('src'); audio.load(); audio.hidden = true;
  if (speechUrl) { URL.revokeObjectURL(speechUrl); speechUrl = null; }
}
async function speakOffline(text) {
  stopSpeech();
  const spokenText = text.replace(/\*/g, '').trim();
  if (!spokenText || shuttingDown || !$('speak').checked) return;
  const version = speechVersion;
  speechController = new AbortController();
  try {
    const response = await checked(await fetch('/api/speech', {method: 'POST', signal: speechController.signal,
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify({text: spokenText})}));
    const blob = await response.blob();
    if (version !== speechVersion || shuttingDown || !$('speak').checked) return;
    speechUrl = URL.createObjectURL(blob);
    const audio = $('speechAudio'); audio.src = speechUrl; audio.hidden = false;
    try { await audio.play(); }
    catch (e) { if (version === speechVersion) notice('音声を生成しました。音声プレーヤーの再生ボタンを押してください。'); }
  } catch (e) {
    if (e.name !== 'AbortError' && version === speechVersion) notice(`音声読み上げ: ${e.message}`);
  }
}
function notice(text = '') { if (shuttingDown) return; $('notice').textContent = text; $('notice').hidden = !text; }
function updateControls() {
  if (shuttingDown) {
    document.querySelectorAll('button, input, select, textarea').forEach(el => { el.disabled = true; });
    return;
  }
  const blocked = busy || !!recording || recordStarting;
  $('send').disabled = blocked;
  $('clear').disabled = blocked;
  $('record').disabled = busy || recordStarting;
  $('micSelect').disabled = blocked;
  $('prompt').disabled = busy;
  $('capture').disabled = busy || !cameraStream;
  $('removeImage').disabled = busy;
}
async function checked(response) {
  if (!response.ok) {
    let detail;
    try { const data = await response.json(); detail = data.error?.message || data.error; } catch (_) { /* status below */ }
    throw new Error(detail || `HTTP ${response.status}`);
  }
  return response;
}
async function health() {
  if (shuttingDown) return;
  try {
    await checked(await fetch('/api/health', {signal: requests.signal}));
    if (shuttingDown) return;
    $('health').textContent = '● Gemma4 / Whisper 接続済み'; $('health').className = 'ready';
  } catch (_) { if (!shuttingDown) { $('health').textContent = '○ モデルサーバー未接続'; $('health').className = ''; } }
}
async function devices() {
  if (!navigator.mediaDevices) return;
  const list = await navigator.mediaDevices.enumerateDevices();
  for (const [id, kind, label] of [['cameraSelect', 'videoinput', 'カメラ'], ['micSelect', 'audioinput', 'マイク']]) {
    const select = $(id), value = select.value;
    select.replaceChildren(new Option(`既定の${label}`, ''));
    list.filter(d => d.kind === kind).forEach((d, i) => select.add(new Option(d.label || `${label} ${i + 1}`, d.deviceId)));
    if ([...select.options].some(o => o.value === value)) select.value = value;
  }
}
function requireMedia() {
  if (!navigator.mediaDevices?.getUserMedia) throw new Error('カメラ・マイクには localhost または HTTPS でアクセスしてください。');
}
function stopCamera() {
  cameraStream?.getTracks().forEach(t => t.stop()); cameraStream = null;
  $('video').srcObject = null; $('cameraPlaceholder').hidden = false;
  $('cameraToggle').textContent = 'カメラを開始'; $('capture').disabled = true;
}
async function startCamera() {
  requireMedia(); stopCamera();
  const deviceId = $('cameraSelect').value;
  const stream = await navigator.mediaDevices.getUserMedia({video: {width: {ideal: 1280}, height: {ideal: 720}, ...(deviceId ? {deviceId: {exact: deviceId}} : {})}, audio: false});
  if (shuttingDown) { stream.getTracks().forEach(t => t.stop()); return; }
  cameraStream = stream; $('video').srcObject = stream;
  await $('video').play(); $('cameraPlaceholder').hidden = true;
  $('cameraToggle').textContent = 'カメラを停止'; $('capture').disabled = false;
  stream.getVideoTracks()[0].addEventListener('ended', stopCamera);
  await devices();
}
function frame() {
  const v = $('video');
  if (!cameraStream || v.readyState < 2 || !v.videoWidth) throw new Error('カメラの映像を待ってから撮影してください。');
  const canvas = document.createElement('canvas'); canvas.width = canvas.height = 480;
  const ctx = canvas.getContext('2d'); ctx.fillStyle = '#000'; ctx.fillRect(0, 0, 480, 480);
  const scale = Math.min(480 / v.videoWidth, 480 / v.videoHeight);
  const w = v.videoWidth * scale, h = v.videoHeight * scale;
  ctx.drawImage(v, (480 - w) / 2, (480 - h) / 2, w, h);
  return canvas.toDataURL('image/jpeg', .9);
}
function setAttachment(value) { attachment = value; $('attachment').hidden = !value; if (value) $('snapshot').src = value; else $('snapshot').removeAttribute('src'); }
$('cameraToggle').onclick = async () => {
  $('cameraToggle').disabled = true;
  try { notice(); if (cameraStream) stopCamera(); else await startCamera(); }
  catch (e) { stopCamera(); notice(`カメラ: ${e.message}`); }
  finally { $('cameraToggle').disabled = shuttingDown; }
};
$('cameraSelect').onchange = async () => { if (cameraStream) { try { await startCamera(); } catch (e) { stopCamera(); notice(e.message); } } };
$('capture').onclick = () => { try { setAttachment(frame()); notice(); } catch (e) { notice(e.message); } };
$('removeImage').onclick = () => setAttachment(null);
function message(role, text, image) {
  $('messages').querySelector('.welcome')?.remove();
  const div = document.createElement('div'); div.className = `message ${role}`;
  const title = document.createElement('strong'); title.textContent = role === 'user' ? 'あなた' : 'Gemma4'; div.append(title);
  if (image) { const img = document.createElement('img'); img.src = image; img.alt = '送信した画像'; div.append(img); }
  const span = document.createElement('span'); span.textContent = text; div.append(span); $('messages').append(div);
  $('messages').scrollTop = $('messages').scrollHeight;
  return {div, span};
}
function boundedHistory() {
  let result = history.slice(-8);
  while (result.length && result.reduce((n, m) => n + m.content.length, 0) + $('prompt').value.length > 6000) result = result.slice(2);
  return result;
}
async function send(event) {
  event.preventDefault();
  if (shuttingDown || busy || recording || recordStarting) return;
  const text = $('prompt').value.trim();
  if (!text) { notice('質問を入力するか、マイクで録音してください。'); return; }
  let image;
  try { image = $('autoFrame').checked ? frame() : attachment; } catch (e) { notice(e.message); return; }
  stopSpeech(); busy = true; updateControls(); notice();
  const previous = boundedHistory();
  message('user', text, image); const output = message('assistant', '考えています…');
  let answer = '', done = false, finishReason = null;
  const start = performance.now();
  try {
    const response = await checked(await fetch('/api/chat', {method: 'POST', signal: requests.signal, headers: {'Content-Type': 'application/json'}, body: JSON.stringify({text, image, history: previous})}));
    const reader = response.body.getReader(), decoder = new TextDecoder(); let buffer = '';
    function parseLine(line) {
      if (!line.startsWith('data:')) return;
      const value = line.slice(5).trim(); if (value === '[DONE]') { done = true; return; }
      const data = JSON.parse(value);
      if (data.error) throw new Error(data.error.message || data.error);
      const choice = data.choices?.[0];
      if (choice?.finish_reason) finishReason = choice.finish_reason;
      const delta = choice?.delta?.content;
      if (delta) { answer += delta; output.span.textContent = answer; $('messages').scrollTop = $('messages').scrollHeight; }
    }
    try {
      while (true) {
        const chunk = await reader.read();
        buffer += decoder.decode(chunk.value || new Uint8Array(), {stream: !chunk.done});
        let index;
        while ((index = buffer.indexOf('\n')) !== -1) { parseLine(buffer.slice(0, index).trim()); buffer = buffer.slice(index + 1); }
        if (chunk.done) { if (buffer.trim()) parseLine(buffer.trim()); break; }
      }
    } finally { await reader.cancel().catch(() => {}); reader.releaseLock(); }
    if (!done) throw new Error('応答の途中で接続が切れました。再送信してください。');
    if (!answer.trim()) throw new Error('回答が空でした。質問を短くして再送信してください。');
    history = [...previous, {role: 'user', content: text}, {role: 'assistant', content: answer}];
    $('prompt').value = ''; setAttachment(null);
    const meta = document.createElement('span'); meta.className = 'meta'; meta.textContent = `${((performance.now() - start) / 1000).toFixed(1)}秒${finishReason === 'length' ? ' · 出力上限に達しました' : ''}`; output.div.append(meta);
    if ($('speak').checked) void speakOffline(answer);
  } catch (e) { output.span.textContent = answer || '回答を取得できませんでした。'; notice(e.message); }
  finally { busy = false; updateControls(); $('prompt').focus(); }
}
$('form').onsubmit = send;
$('prompt').onkeydown = e => { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing && e.keyCode !== 229) { e.preventDefault(); $('form').requestSubmit(); } };
$('clear').onclick = () => { history = []; $('messages').replaceChildren(); setAttachment(null); notice(); stopSpeech(); };
$('speak').onchange = () => { if (!$('speak').checked) stopSpeech(); };
// Average samples into 16 kHz bins, then encode little-endian PCM WAV.
function wavBlob(chunks, sampleRate) {
  const length = chunks.reduce((n, c) => n + c.length, 0), source = new Float32Array(length); let pos = 0;
  for (const chunk of chunks) { source.set(chunk, pos); pos += chunk.length; }
  const count = Math.floor(length * 16000 / sampleRate), buffer = new ArrayBuffer(44 + count * 2), view = new DataView(buffer);
  const ascii = (offset, str) => [...str].forEach((c, i) => view.setUint8(offset + i, c.charCodeAt(0)));
  ascii(0, 'RIFF'); view.setUint32(4, 36 + count * 2, true); ascii(8, 'WAVE'); ascii(12, 'fmt '); view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true); view.setUint32(24, 16000, true); view.setUint32(28, 32000, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true); ascii(36, 'data'); view.setUint32(40, count * 2, true);
  for (let i = 0; i < count; i++) {
    const begin = Math.floor(i * sampleRate / 16000), end = Math.min(length, Math.max(begin + 1, Math.floor((i + 1) * sampleRate / 16000)));
    let sum = 0; for (let j = begin; j < end; j++) sum += source[j];
    const value = Math.max(-1, Math.min(1, sum / (end - begin)));
    view.setInt16(44 + i * 2, value * (value < 0 ? 32768 : 32767), true);
  }
  return new Blob([buffer], {type: 'audio/wav'});
}
async function startRecording() {
  if (shuttingDown) return;
  requireMedia(); recordStarting = true; updateControls();
  let stream, ctx;
  try {
    const deviceId = $('micSelect').value;
    stream = await navigator.mediaDevices.getUserMedia({audio: {channelCount: 1, echoCancellation: true, ...(deviceId ? {deviceId: {exact: deviceId}} : {})}});
    if (shuttingDown) { stream.getTracks().forEach(t => t.stop()); return; }
    ctx = new AudioContext(); await ctx.resume();
    if (shuttingDown) { stream.getTracks().forEach(t => t.stop()); await ctx.close(); return; }
    const source = ctx.createMediaStreamSource(stream), processor = ctx.createScriptProcessor(4096, 1, 1), mute = ctx.createGain(); mute.gain.value = 0;
    const state = {stream, ctx, source, processor, mute, chunks: [], samples: 0, start: performance.now()};
    processor.onaudioprocess = e => {
      if (recording !== state) return;
      const input = e.inputBuffer.getChannelData(0), remaining = Math.max(0, Math.floor(ctx.sampleRate * 30) - state.samples);
      const chunk = new Float32Array(input.subarray(0, remaining)); state.chunks.push(chunk); state.samples += chunk.length;
      if (state.samples >= ctx.sampleRate * 30) void stopRecording();
    };
    source.connect(processor); processor.connect(mute); mute.connect(ctx.destination);
    recording = state;
    state.timer = setInterval(() => { $('recordStatus').textContent = `${Math.min(30, (performance.now() - state.start) / 1000).toFixed(0)} / 30秒`; }, 200);
    state.limit = setTimeout(() => void stopRecording(), 30000);
    $('record').textContent = '■ 録音を停止'; $('record').classList.add('recording');
    stream.getAudioTracks()[0].addEventListener('ended', () => { if (recording === state) void stopRecording(); });
    await devices();
  } catch (e) { if (recording) await stopRecording(); else { stream?.getTracks().forEach(t => t.stop()); if (ctx) await ctx.close(); } throw e; }
  finally { recordStarting = false; updateControls(); }
}
async function stopRecording() {
  const state = recording; if (!state) return;
  recording = null; busy = true; updateControls();
  clearInterval(state.timer); clearTimeout(state.limit); state.processor.onaudioprocess = null;
  state.source.disconnect(); state.processor.disconnect(); state.mute.disconnect(); state.stream.getTracks().forEach(t => t.stop());
  $('record').textContent = '● 録音を開始'; $('record').classList.remove('recording'); $('recordStatus').textContent = '文字起こし中…';
  try {
    await state.ctx.close();
    if (state.samples / state.ctx.sampleRate < .1) throw new Error('録音が短すぎます。もう一度録音してください。');
    const blob = wavBlob(state.chunks, state.ctx.sampleRate);
    if (shuttingDown) return;
    const response = await checked(await fetch('/api/transcribe', {method: 'POST', signal: requests.signal, headers: {'Content-Type': 'audio/wav'}, body: blob}));
    const data = await response.json();
    if (!data.text?.trim() || data.no_speech_prob > .8) throw new Error('音声を認識できませんでした。マイクと音量を確認してください。');
    const text = [$('prompt').value.trim(), data.text.trim()].filter(Boolean).join('\n');
    if (text.length > 6000) throw new Error('入力が6000文字を超えます。メッセージを短くしてください。');
    $('prompt').value = text; $('recordStatus').textContent = '文字起こし完了'; $('prompt').focus();
  } catch (e) { notice(`音声認識: ${e.message}`); $('recordStatus').textContent = ''; }
  finally { busy = false; updateControls(); }
}
$('record').onclick = async () => { try { notice(); if (recording) await stopRecording(); else await startRecording(); } catch (e) { notice(`マイク: ${e.message}`); } };
window.addEventListener('pagehide', () => { stopCamera(); recording?.stream.getTracks().forEach(t => t.stop()); stopSpeech(); });

void health(); const healthTimer = setInterval(health, 15000); void devices().catch(() => {});
$('shutdown').onclick = async () => {
  if (shuttingDown) return;
  notice(); shuttingDown = true; updateControls();
  $('shutdown').textContent = '終了中…';
  $('health').textContent = '終了を要求しています…'; $('health').className = '';
  requests.abort(); stopCamera(); stopSpeech();
  const state = recording; recording = null;
  if (state) {
    clearInterval(state.timer); clearTimeout(state.limit); state.processor.onaudioprocess = null;
    state.source.disconnect(); state.processor.disconnect(); state.mute.disconnect();
    state.stream.getTracks().forEach(t => t.stop()); void state.ctx.close().catch(() => {});
  }
  $('record').textContent = '● 録音を開始'; $('record').classList.remove('recording'); $('recordStatus').textContent = '';
  try {
    await checked(await fetch('/api/shutdown', {method: 'POST', headers: {'X-Gemma4-Action': 'shutdown'}}));
    clearInterval(healthTimer);
    $('health').textContent = '終了処理を受け付けました';
    $('shutdown').textContent = '終了要求済み';
    $('notice').textContent = 'アプリの終了処理を開始しました。このタブを閉じてください。'; $('notice').hidden = false;
  } catch (e) {
    shuttingDown = false; requests = new AbortController();
    document.querySelectorAll('button, input, select, textarea').forEach(el => { el.disabled = false; });
    updateControls(); $('shutdown').textContent = '終了';
    notice(`終了要求に失敗しました: ${e.message}。再試行するか、起動した端末で Ctrl+C を押してください。`);
    void health();
  }
};
