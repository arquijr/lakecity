const CHUNK_SIZE = 16384; // Bloques de 16 KB para máxima velocidad y estabilidad
const peer = new Peer();
let conn = null;

// Control de transferencia activa
let incomingFileHeader = null;
let receivedChunks = [];
let receivedSize = 0;
let fileToSend = null;

// Elementos DOM
const myIdSpan = document.getElementById('my-id');
const qrcodeDiv = document.getElementById('qrcode');
const peerIdInput = document.getElementById('peer-id-input');
const connectBtn = document.getElementById('connect-btn');
const pinDisplay = document.getElementById('pin-display');
const chatBox = document.getElementById('chat-box');
const messageInput = document.getElementById('message-input');
const sendMsgBtn = document.getElementById('send-msg-btn');
const fileInput = document.getElementById('file-input');
const sendFileBtn = document.getElementById('send-file-btn');

const requestBanner = document.getElementById('request-banner');
const requestText = document.getElementById('request-text');
const acceptBtn = document.getElementById('accept-btn');
const rejectBtn = document.getElementById('reject-btn');

const progressContainer = document.getElementById('progress-container');
const progressFill = document.getElementById('progress-fill');
const progressText = document.getElementById('progress-text');

// 1. Mostrar ID y Generar Código QR
peer.on('open', (id) => {
  myIdSpan.textContent = id;
  qrcodeDiv.innerHTML = '';
  new QRCode(qrcodeDiv, { text: id, width: 110, height: 110 });
});

// 1b. Errores del peer (ID inválido, peer no disponible, servidor caído, etc.)
peer.on('error', (err) => {
  console.error('PeerJS error:', err);

  const messages = {
    'peer-unavailable': 'El ID del otro dispositivo no existe o no está disponible.',
    'invalid-id': 'El ID ingresado no es válido.',
    'unavailable-id': 'Ese ID ya está en uso, intenta recargar la página.',
    'network': 'Problema de red al conectar con el servidor de señalización.',
    'server-error': 'El servidor de señalización no está disponible en este momento.',
    'socket-error': 'Error de conexión con el servidor de señalización.',
    'socket-closed': 'Se perdió la conexión con el servidor de señalización.',
    'browser-incompatible': 'Tu navegador no soporta las funciones necesarias (WebRTC).'
  };

  const friendly = messages[err.type] || 'Ocurrió un error de conexión inesperado.';
  appendSystemMsg(`⚠️ ${friendly}`);

  // Si el error ocurrió mientras intentábamos conectar, reactivamos el botón
  connectBtn.disabled = false;
});

// 2. Conexiones Entrantes y Salientes
peer.on('connection', (incomingConn) => {
  // Si ya hay una conexión activa, rechazamos explícitamente la nueva
  // en vez de sobrescribir `conn` en silencio (lo que dejaría la conexión
  // anterior "huérfana" y podría mezclar mensajes de dos peers distintos).
  if (conn && conn.open) {
    appendSystemMsg(`Conexión entrante de "${incomingConn.peer}" rechazada: ya hay una sesión activa.`);
    incomingConn.on('open', () => {
      incomingConn.close();
    });
    return;
  }

  conn = incomingConn;
  setupConnection();
});

connectBtn.addEventListener('click', () => {
  const targetId = peerIdInput.value.trim();
  if (targetId) {
    connectBtn.disabled = true;
    conn = peer.connect(targetId);
    setupConnection();
  }
});

// 3. Configurar Conexión Segura
function setupConnection() {
  conn.on('open', () => {
    connectBtn.disabled = false;
    enableControls();
    generateSecurityPIN();
    appendSystemMsg('Conexión P2P directa (cifrada por WebRTC) establecida.');
  });

  conn.on('data', (data) => handleIncomingData(data));
  conn.on('close', () => {
    appendSystemMsg('El otro dispositivo se ha desconectado.');
    disableControls();
    connectBtn.disabled = false;
    conn = null;
  });
  conn.on('error', (err) => {
    console.error('Connection error:', err);
    appendSystemMsg('⚠️ Error en la conexión con el otro dispositivo.');
    connectBtn.disabled = false;
  });
}

// Genera un PIN visual único basado en la pareja de IDs
function generateSecurityPIN() {
  const combined = [peer.id, conn.peer].sort().join('');
  let hash = 0;
  for (let i = 0; i < combined.length; i++) {
    hash = (hash << 5) - hash + combined.charCodeAt(i);
    hash |= 0;
  }
  const pin = Math.abs(hash % 9000) + 1000;
  pinDisplay.style.display = 'block';
  pinDisplay.textContent = `🔑 Código PIN de seguridad: ${pin}`;
}

// 4. Manejador Central de Datos
function handleIncomingData(data) {
  // A. Mensajes de Chat
  if (data.type === 'chat') {
    appendMsg('Otro', data.content, false);
  }
  
  // B. Solicitud de Envío de Archivo (Handshake)
  else if (data.type === 'file-request') {
    incomingFileHeader = data;
    requestText.textContent = `¿Aceptar "${data.name}" (${formatBytes(data.size)})?`;
    requestBanner.style.display = 'block';
  }
  
  // C. Respuesta a Solicitud de Archivo
  else if (data.type === 'file-response') {
    if (data.accepted) {
      appendSystemMsg('Solicitud aceptada. Iniciando envío...');
      startChunkedUpload();
    } else {
      appendSystemMsg('El usuario rechazó la transferencia del archivo.');
      fileToSend = null;
    }
  }

  // D. Recepción de Bloques (Chunks)
  else if (data.type === 'file-chunk') {
    receivedChunks.push(data.chunk);
    receivedSize += data.chunk.byteLength;
    
    const progress = Math.round((receivedSize / incomingFileHeader.size) * 100);
    updateProgress(progress);

    if (receivedSize >= incomingFileHeader.size) {
      finishFileDownload();
    }
  }
}

// 5. Enviar Mensaje de Texto
sendMsgBtn.addEventListener('click', () => {
  const text = messageInput.value.trim();
  if (text && conn) {
    conn.send({ type: 'chat', content: text });
    appendMsg('Tú', text, true);
    messageInput.value = '';
  }
});

// 6. Solicitud de Envío de Archivo
sendFileBtn.addEventListener('click', () => {
  const file = fileInput.files[0];
  if (!file || !conn) return;

  fileToSend = file;
  const cleanName = sanitizeFilename(file.name);

  // Enviamos solo la solicitud previa (Handshake)
  conn.send({
    type: 'file-request',
    name: cleanName,
    size: file.size,
    mime: file.type
  });

  appendSystemMsg(`Esperando que el receptor acepte "${cleanName}"...`);
});

// Botones de Aceptar / Rechazar
acceptBtn.addEventListener('click', () => {
  requestBanner.style.display = 'none';
  receivedChunks = [];
  receivedSize = 0;
  showProgress();
  conn.send({ type: 'file-response', accepted: true });
});

rejectBtn.addEventListener('click', () => {
  requestBanner.style.display = 'none';
  conn.send({ type: 'file-response', accepted: false });
  incomingFileHeader = null;
});

// 7. Algoritmo de Fragmentación (Chunking) con control de backpressure
const BUFFERED_AMOUNT_HIGH_THRESHOLD = CHUNK_SIZE * 16; // ~256 KB en cola como máximo
const BACKPRESSURE_CHECK_INTERVAL_MS = 20;

function startChunkedUpload() {
  if (!fileToSend) return;

  showProgress();
  let offset = 0;
  const reader = new FileReader();

  // Umbral bajo el cual consideramos que el datachannel puede recibir más datos
  const dataChannel = conn.dataChannel;
  if (dataChannel) {
    dataChannel.bufferedAmountLowThreshold = CHUNK_SIZE * 4;
  }

  reader.onload = (e) => {
    conn.send({
      type: 'file-chunk',
      chunk: e.target.result
    });

    offset += e.target.result.byteLength;
    const progress = Math.round((offset / fileToSend.size) * 100);
    updateProgress(progress);

    if (offset < fileToSend.size) {
      scheduleNextChunk();
    } else {
      appendSystemMsg(`¡Archivo "${fileToSend.name}" enviado con éxito!`);
      fileInput.value = '';
      fileToSend = null;
      hideProgress();
    }
  };

  reader.onerror = () => {
    appendSystemMsg(`⚠️ Error leyendo "${fileToSend?.name}", transferencia cancelada.`);
    fileToSend = null;
    hideProgress();
  };

  function scheduleNextChunk() {
    // Si el buffer del datachannel está muy lleno, esperamos antes de leer/enviar
    // el siguiente chunk para no saturar la memoria ni la red (backpressure).
    const buffered = dataChannel ? dataChannel.bufferedAmount : 0;
    if (buffered > BUFFERED_AMOUNT_HIGH_THRESHOLD) {
      setTimeout(scheduleNextChunk, BACKPRESSURE_CHECK_INTERVAL_MS);
      return;
    }
    readNextChunk();
  }

  function readNextChunk() {
    const slice = fileToSend.slice(offset, offset + CHUNK_SIZE);
    reader.readAsArrayBuffer(slice);
  }

  readNextChunk();
}

// 8. Reconstrucción y Descarga del Archivo Final
function finishFileDownload() {
  const blob = new Blob(receivedChunks, { type: incomingFileHeader.mime });
  const url = URL.createObjectURL(blob);
  
  const a = document.createElement('a');
  a.href = url;
  a.download = incomingFileHeader.name;
  a.textContent = `💾 Descargar ${incomingFileHeader.name} (${formatBytes(incomingFileHeader.size)})`;
  a.style.display = 'block';
  a.style.marginTop = '6px';
  a.style.fontWeight = 'bold';

  // Liberamos la URL del Blob una vez que el usuario ya inició la descarga,
  // o tras un tiempo prudencial si nunca hace clic, para no acumular memoria
  // en sesiones con muchas transferencias.
  let urlRevoked = false;
  const revokeOnce = () => {
    if (!urlRevoked) {
      urlRevoked = true;
      URL.revokeObjectURL(url);
    }
  };
  a.addEventListener('click', () => setTimeout(revokeOnce, 1000));
  setTimeout(revokeOnce, 5 * 60 * 1000); // liberación de respaldo a los 5 min

  const div = document.createElement('div');
  div.className = 'msg peer-msg';
  div.innerHTML = `<strong>Archivo recibido:</strong>`;
  div.appendChild(a);
  
  chatBox.appendChild(div);
  chatBox.scrollTop = chatBox.scrollHeight;

  // Resetear estados
  hideProgress();
  receivedChunks = [];
  receivedSize = 0;
  incomingFileHeader = null;
}

// Auxiliares de interfaz y sanitización
function sanitizeFilename(name) {
  return name.replace(/[^a-zA-Z0-9_.-]/g, '_');
}

function formatBytes(bytes) {
  if (bytes === 0) return '0 Bytes';
  const k = 1024;
  const sizes = ['Bytes', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}

function showProgress() {
  progressContainer.style.display = 'block';
  updateProgress(0);
}

function updateProgress(percent) {
  progressFill.style.width = percent + '%';
  progressText.textContent = percent + '%';
}

function hideProgress() {
  setTimeout(() => { progressContainer.style.display = 'none'; }, 1500);
}

function appendMsg(sender, text, isMine) {
  const div = document.createElement('div');
  div.className = `msg ${isMine ? 'my-msg' : 'peer-msg'}`;

  const strong = document.createElement('strong');
  strong.textContent = `${sender}: `;

  // Usamos un nodo de texto para el contenido del peer: nunca se interpreta como HTML,
  // así un peer malicioso no puede inyectar <script>, <img onerror>, etc.
  div.appendChild(strong);
  div.appendChild(document.createTextNode(text));

  chatBox.appendChild(div);
  chatBox.scrollTop = chatBox.scrollHeight;
}

function appendSystemMsg(text) {
  const div = document.createElement('div');
  div.className = 'msg sys-msg';
  div.textContent = text;
  chatBox.appendChild(div);
  chatBox.scrollTop = chatBox.scrollHeight;
}

function enableControls() {
  messageInput.disabled = false;
  sendMsgBtn.disabled = false;
  fileInput.disabled = false;
  sendFileBtn.disabled = false;
}

function disableControls() {
  messageInput.disabled = true;
  sendMsgBtn.disabled = true;
  fileInput.disabled = true;
  sendFileBtn.disabled = true;
  pinDisplay.style.display = 'none';
}