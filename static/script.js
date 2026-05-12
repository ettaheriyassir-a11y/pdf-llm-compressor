/* =============================================================
   PDF → LLM Compressor  |  script.js
   ============================================================= */

document.addEventListener('DOMContentLoaded', () => {

  // ── DOM refs ───────────────────────────────────────────────
  const dropzone          = document.getElementById('dropzone');
  const fileInput         = document.getElementById('fileInput');
  const loadingOverlay    = document.getElementById('loadingOverlay');
  const loadingText       = document.getElementById('loadingText');
  const resultsSection    = document.getElementById('resultsSection');
  const filePill          = document.getElementById('filePill');
  const filePillName      = document.getElementById('filePillName');

  // Metrics
  const originalTokensEl  = document.getElementById('originalTokens');
  const compressedTokensEl= document.getElementById('compressedTokens');
  const savingsPercentEl  = document.getElementById('savingsPercent');
  const savingsBar        = document.getElementById('savingsBar');

  // Output
  const xmlOutput         = document.getElementById('xmlOutput');
  const xmlView           = document.getElementById('xmlView');
  const chunksView        = document.getElementById('chunksView');
  const tabXml            = document.getElementById('tabXml');
  const tabChunks         = document.getElementById('tabChunks');
  const copyBtn           = document.getElementById('copyBtn');
  const downloadBtn       = document.getElementById('downloadBtn');

  // ── State ──────────────────────────────────────────────────
  let currentTab      = 'xml';
  let lastFilename    = 'output';
  let lastXmlContent  = '';

  // ── Gap 5: real SSE stage labels ──────────────────────────
  const STAGE_LABELS = {
    reading_pdf:    'Reading PDF structure…',
    building_dict:  'Building compression dictionary…',
    generating_xml: 'Generating XML…',
    chunking:       'Creating RAG chunks…',
    done:           'Complete!',
  };

  function applyStage(stage) {
    if (stage.startsWith('ocr_processing:')) {
      const [, n, total] = stage.split(':');
      loadingText.textContent = `Running OCR on page ${n} of ${total}…`;
    } else if (stage.startsWith('extracting_page:')) {
      const [, n, total] = stage.split(':');
      loadingText.textContent = `Extracting page ${n} of ${total}…`;
    } else {
      loadingText.textContent = STAGE_LABELS[stage] ?? stage;
    }
  }

  // ── Drag-and-drop wiring ───────────────────────────────────
  ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(ev => {
    dropzone.addEventListener(ev, e => { e.preventDefault(); e.stopPropagation(); }, false);
  });

  ['dragenter', 'dragover'].forEach(ev =>
    dropzone.addEventListener(ev, () => dropzone.classList.add('drag-over'))
  );
  ['dragleave', 'drop'].forEach(ev =>
    dropzone.addEventListener(ev, () => dropzone.classList.remove('drag-over'))
  );

  dropzone.addEventListener('drop', e => {
    const files = e.dataTransfer?.files;
    if (files?.length) handleFile(files[0]);
  });

  fileInput.addEventListener('change', e => {
    if (e.target.files?.length) handleFile(e.target.files[0]);
  });

  // ── File validation & kick-off ──────────────────────────────
  function handleFile(file) {
    if (file.type !== 'application/pdf') {
      showNotification('Only PDF files are supported.', 'error');
      return;
    }
    if (file.size > 20 * 1024 * 1024) {
      showNotification('File is too large. Maximum is 20 MB.', 'error');
      return;
    }
    uploadFile(file);
  }

  // ── Main upload using SSE streaming (Gap 5) ─────────────────
  async function uploadFile(file) {
    lastFilename = file.name.replace(/\.pdf$/i, '');

    // Show file pill
    filePillName.textContent = file.name;
    filePill.classList.add('visible');

    // Show loading
    loadingText.textContent = 'Uploading…';
    loadingOverlay.classList.add('visible');
    resultsSection.style.display = 'none';
    resultsSection.classList.add('hidden');

    const formData = new FormData();
    formData.append('file', file);
    formData.append('dictCompression',       document.getElementById('dictCompression').checked);
    formData.append('semanticDeduplication', document.getElementById('semanticDeduplication').checked);
    formData.append('minifyXml',             document.getElementById('minifyXml').checked);
    // Gap 4: send chunk size
    formData.append('chunkSize', parseInt(document.getElementById('chunkSize').value, 10) || 800);

    try {
      const response = await fetch('/api/v1/compress/stream', {
        method: 'POST',
        body: formData,
      });

      if (!response.ok || !response.body) {
        throw new Error(`Server error ${response.status}`);
      }

      // Read the SSE stream via ReadableStream
      const reader  = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer    = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop();   // keep last (possibly incomplete) line

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          const data = JSON.parse(line.slice(6));

          if (data.type === 'progress') {
            applyStage(data.stage);
          } else if (data.type === 'done') {
            renderResults(data);
          } else if (data.type === 'error') {
            throw new Error(data.message);
          }
        }
      }

    } catch (err) {
      console.error(err);
      showNotification(err.message || 'Processing failed. Check the console for details.', 'error');
    } finally {
      loadingOverlay.classList.remove('visible');
      fileInput.value = '';
    }
  }

  // ── Render results ──────────────────────────────────────────
  function renderResults(data) {
    const orig    = data.original_tokens   ?? 0;
    const comp    = data.compressed_tokens ?? 0;
    const savings = orig > 0 ? Math.max(0, Math.round((1 - comp / orig) * 100)) : 0;

    animateCounter(originalTokensEl,   orig);
    animateCounter(compressedTokensEl, comp);
    savingsPercentEl.textContent = savings + '%';

    requestAnimationFrame(() => { savingsBar.style.width = savings + '%'; });

    lastXmlContent = data.xml_content ?? '';
    xmlOutput.value = lastXmlContent;

    renderChunks(data.chunks ?? []);
    switchTab('xml');

    resultsSection.style.display = 'flex';
    resultsSection.classList.remove('hidden');
  }

  // ── Chunk renderer ──────────────────────────────────────────
  function renderChunks(chunks) {
    chunksView.innerHTML = '';
    if (!chunks.length) {
      chunksView.innerHTML = '<p style="text-align:center; color:var(--text-3); padding:32px 0;">No chunks generated.</p>';
      return;
    }
    chunks.forEach((chunk, i) => {
      const tokenCount = chunk.tokens;
      const card = document.createElement('div');
      card.className = 'chunk-card';
      card.innerHTML = `
        <div class="chunk-header">
          <span class="badge badge-primary">Chunk ${i + 1}</span>
          <span style="font-size:0.72rem; color:var(--text-3);">${tokenCount.toLocaleString()} tokens</span>
        </div>
        <div class="chunk-body">${escapeHtml(chunk.text)}</div>`;
      chunksView.appendChild(card);
    });
  }

  // ── Tab switching ───────────────────────────────────────────
  function switchTab(tab) {
    currentTab = tab;
    if (tab === 'xml') {
      tabXml.classList.add('active');
      tabChunks.classList.remove('active');
      xmlView.classList.remove('hidden');
      chunksView.classList.add('hidden');
    } else {
      tabChunks.classList.add('active');
      tabXml.classList.remove('active');
      chunksView.classList.remove('hidden');
      xmlView.classList.add('hidden');
    }
  }

  tabXml.addEventListener('click',    () => switchTab('xml'));
  tabChunks.addEventListener('click', () => switchTab('chunks'));

  // ── Copy to clipboard ───────────────────────────────────────
  copyBtn.addEventListener('click', async () => {
    let text = '';
    if (currentTab === 'xml') {
      text = xmlOutput.value;
    } else {
      text = Array.from(chunksView.querySelectorAll('.chunk-body'))
        .map(el => el.textContent)
        .join('\n\n--- CHUNK SEPARATOR ---\n\n');
    }
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      flashBtn(copyBtn, '✓ Copied!');
    } catch {
      showNotification('Could not access clipboard.', 'error');
    }
  });

  // ── Download XML ────────────────────────────────────────────
  downloadBtn.addEventListener('click', () => {
    if (!lastXmlContent) return;
    const blob = new Blob([lastXmlContent], { type: 'application/xml' });
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement('a');
    a.href     = url;
    a.download = `${lastFilename}_compressed.xml`;
    a.click();
    URL.revokeObjectURL(url);
  });

  // ── Helpers ─────────────────────────────────────────────────

  function animateCounter(el, target) {
    const duration = 700;
    const start    = performance.now();
    const from     = parseInt(el.textContent.replace(/\D/g, '')) || 0;
    function step(now) {
      const progress = Math.min((now - start) / duration, 1);
      const eased    = 1 - Math.pow(1 - progress, 3);
      el.textContent = Math.round(from + (target - from) * eased).toLocaleString();
      if (progress < 1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
  }

  function estimateTokens(text) {
    return Math.round(text.trim().split(/\s+/).length / 0.75);
  }

  function escapeHtml(str) {
    return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function flashBtn(btn, label) {
    const orig = btn.innerHTML;
    btn.textContent = label;
    btn.style.color = 'var(--green)';
    setTimeout(() => { btn.innerHTML = orig; btn.style.color = ''; }, 2000);
  }

  function showNotification(message, type = 'info') {
    const n = document.createElement('div');
    n.style.cssText = `
      position:fixed; bottom:24px; right:24px; z-index:9999;
      padding:12px 20px; border-radius:10px; font-size:0.85rem; font-weight:500;
      backdrop-filter:blur(12px); border:1px solid;
      background: ${type === 'error' ? 'rgba(239,68,68,0.15)' : 'rgba(99,102,241,0.15)'};
      border-color: ${type === 'error' ? 'rgba(239,68,68,0.4)' : 'rgba(99,102,241,0.4)'};
      color: ${type === 'error' ? '#fca5a5' : 'var(--primary)'};
      animation: fadeInUp 0.3s ease-out forwards;
      box-shadow: 0 4px 24px rgba(0,0,0,0.4);
    `;
    n.textContent = message;
    document.body.appendChild(n);
    setTimeout(() => n.remove(), 5000);
  }

});
