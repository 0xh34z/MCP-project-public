const state = {
  conversationId: null,
  loadingConversationId: null,
  sseSource: null,
  sseManuallyClosed: false,
  sseConnected: false,
  pollIntervalId: null,
  pollInFlight: false,
  stickToBottom: true,
  isWaitingForLlm: false,
  isCreatingConversation: false,
  autoApprove: false,
  pendingApprovals: [],
  pendingClarifications: [],
  manualAutoApprove: false,
  scheduledAutoApproveActive: false,
  mcpOverrideHash: '',
  llmProviders: [],
  llmModels: [],
  llmSelection: {
    providerId: 0,
    model: '',
  },
  llmSelectionLoaded: false,
  llmAutoMode: false,
  llmRouteMode: 'auto',
  llmModelsMeta: [],
  llmSelectedModelLoaded: null,
  llmLoadingHint: '',
  llmLoadPollId: null,
  pendingJobsCount: 0,
  nextScheduledAt: null,
  messageThoughtCache: {},
  pendingAttachments: [],
  isPreparingAttachments: false,
  isTerminatingRun: false,
  conversationMessageCache: {},
};

const elements = {
  conversationList: document.getElementById('conversationList'),
  chatMessages: document.getElementById('chatMessages'),
  messageInput: document.getElementById('messageInput'),
  sendButton: document.getElementById('sendButton'),
  attachmentButton: document.getElementById('attachmentButton'),
  fileInput: document.getElementById('fileInput'),
  attachmentList: document.getElementById('attachmentList'),
  newConversationButton: document.getElementById('newConversationButton'),
  queueStatus: document.getElementById('queueStatus'),
  streamStatus: document.getElementById('streamStatus'),
  sidebarToggle: document.getElementById('sidebarToggle'),
  mcpServerList: document.getElementById('mcpServerList'),
  personaInput: document.getElementById('persona'),
  blueprintsInput: document.getElementById('blueprints'),
  saveIdentityButton: document.getElementById('saveIdentityButton'),
  personaUploadLink: document.getElementById('uploadPersonaLink'),
  personaFileInput: document.getElementById('personaFileInput'),
  infrastructureUploadLink: document.getElementById('uploadInfrastructureLink'),
  infrastructureFileInput: document.getElementById('infrastructureFileInput'),
  personaPreview: document.getElementById('personaPreview'),
  blueprintsPreview: document.getElementById('blueprintsPreview'),
  viewPersonaLink: document.getElementById('viewPersonaLink'),
  viewInfrastructureLink: document.getElementById('viewInfrastructureLink'),
  identityDialog: document.getElementById('identityDialog'),
  dialogTitle: document.getElementById('dialogTitle'),
  dialogTextarea: document.getElementById('dialogTextarea'),
  closeDialogButton: document.getElementById('closeDialogButton'),
  saveDialogButton: document.getElementById('saveDialogButton'),
  autoApproveToggle: document.getElementById('autoApproveToggle'),
  toolApprovalBanner: document.getElementById('toolApprovalBanner'),
  clarificationBanner: document.getElementById('clarificationBanner'),
  autoApproveToggleLabel: document.getElementById('autoApproveToggleLabel'),
  llmProviderSelect: document.getElementById('llmProviderSelect'),
  llmModelSelect: document.getElementById('llmModelSelect'),
  llmModelInput: document.getElementById('llmModelInput'),
  llmRouteSelect: document.getElementById('llmRouteSelect'),
  llmTopbarManual: document.getElementById('llmTopbarManual'),
  llmTopbarAuto: document.getElementById('llmTopbarAuto'),
};

const chatShellRoot = document.querySelector('section[data-user-label][data-assistant-label]');
const messageRoleLabels = {
  user: String(chatShellRoot?.dataset?.userLabel || 'user').trim() || 'user',
  assistant: String(chatShellRoot?.dataset?.assistantLabel || 'assistant').trim() || 'assistant',
};

function updateAssistantLabel(modelName) {
  const nextLabel = String(modelName || state.llmSelection.model || chatShellRoot?.dataset?.assistantLabel || 'assistant').trim();
  if (nextLabel) {
    messageRoleLabels.assistant = nextLabel;
  }
}

function isRouterAutoSelection() {
  const routerEnabled = String(chatShellRoot?.dataset?.routerEnabled || '1') !== '0';
  if (!routerEnabled) return false;

  const trigger = String(chatShellRoot?.dataset?.routerTrigger || 'router').trim().toLowerCase();
  const provider = (state.llmProviders || []).find((item) => Number(item.id || 0) === Number(state.llmSelection.providerId || 0));
  const providerName = String(provider?.name || '').toLowerCase();
  const modelName = String(state.llmSelection.model || '').trim().toLowerCase();
  if (trigger && providerName.includes(trigger)) return true;
  return modelName === 'auto' || modelName === 'router';
}

function normalizeModelEntries(models) {
  const source = Array.isArray(models) ? models : [];
  const entries = source.map((item) => {
    if (typeof item === 'string') {
      return { name: item, loaded: null };
    }
    return {
      name: String(item?.name || item?.id || '').trim(),
      loaded: item?.loaded === true ? true : (item?.loaded === false ? false : null),
    };
  }).filter((entry) => entry.name);

  const sizeRank = (name) => {
    const m = String(name).toLowerCase().match(/(\d+(?:\.\d+)?)\s*b/);
    return m ? Number(m[1]) : Number.POSITIVE_INFINITY;
  };

  entries.sort((a, b) => {
    const aSize = sizeRank(a.name);
    const bSize = sizeRank(b.name);
    if (Number.isFinite(aSize) && Number.isFinite(bSize) && aSize !== bSize) {
      return aSize - bSize;
    }
    if (Number.isFinite(aSize) && !Number.isFinite(bSize)) return -1;
    if (!Number.isFinite(aSize) && Number.isFinite(bSize)) return 1;
    return a.name.localeCompare(b.name, undefined, { sensitivity: 'base' });
  });

  return entries;
}

function refreshSelectedModelLoadedState() {
  const selected = String(state.llmSelection.model || '').trim().toLowerCase();
  if (!selected) {
    state.llmSelectedModelLoaded = null;
    return;
  }
  const match = (state.llmModelsMeta || []).find((entry) => String(entry.name || '').trim().toLowerCase() === selected);
  state.llmSelectedModelLoaded = match ? match.loaded : null;
}

function getAutoRouterProviderId() {
  const trigger = String(chatShellRoot?.dataset?.routerTrigger || 'router').trim().toLowerCase();
  const providers = Array.isArray(state.llmProviders) ? state.llmProviders : [];
  const routerProvider = providers.find((provider) => {
    const name = String(provider?.name || '').toLowerCase();
    return trigger && name.includes(trigger);
  });
  return Number(routerProvider?.id || 0) || Number(state.llmSelection.providerId || 0) || 0;
}

function renderRouteOptions() {
  if (!elements.llmRouteSelect) return;

  const providers = Array.isArray(state.llmProviders) ? state.llmProviders : [];
  const options = ['<option value="auto">Auto mode</option>'];
  providers.forEach((provider) => {
    const id = Number(provider?.id || 0) || 0;
    if (!id) return;
    const label = `${String(provider.name || 'Provider')} (${String(provider.provider_type || 'openai-compatible')})`;
    options.push(`<option value="provider:${id}">${escapeHtml(label)}</option>`);
  });

  elements.llmRouteSelect.innerHTML = options.join('');
  elements.llmRouteSelect.value = state.llmRouteMode || 'auto';
}

function stopModelLoadWatcher() {
  if (state.llmLoadPollId) {
    window.clearInterval(state.llmLoadPollId);
    state.llmLoadPollId = null;
  }
  if (!state.isWaitingForLlm) {
    state.llmLoadingHint = '';
  }
  updateQueueStatus();
}

async function refreshModelLoadedStatus() {
  const providerId = Number(state.llmSelection.providerId || 0) || 0;
  if (!providerId) return;
  try {
    const data = await api('llm_models', { provider_id: providerId });
    state.llmModelsMeta = normalizeModelEntries(data.models || []);
    refreshSelectedModelLoadedState();
    if (state.llmSelectedModelLoaded === true) {
      state.llmLoadingHint = `Model ready in memory: ${state.llmSelection.model}`;
      stopModelLoadWatcher();
    } else {
      updateQueueStatus();
    }
  } catch (error) {
    console.warn('Failed to refresh model loaded status', error);
  }
}

function startModelLoadWatcher() {
  stopModelLoadWatcher();
  if (state.llmRouteMode === 'auto') return;
  if (state.llmSelectedModelLoaded !== false) return;

  const targetModel = String(state.llmSelection.model || '').trim();
  if (!targetModel) return;

  state.llmLoadingHint = `Loading model into memory: ${targetModel}`;
  updateQueueStatus();
  state.llmLoadPollId = window.setInterval(() => {
    if (!state.isWaitingForLlm) {
      stopModelLoadWatcher();
      return;
    }
    refreshModelLoadedStatus();
  }, 3000);
}

function syncLlmTopbarMode() {
  const autoMode = state.llmRouteMode === 'auto';
  state.llmAutoMode = autoMode;

  if (elements.llmTopbarManual) {
    elements.llmTopbarManual.classList.toggle('hidden', autoMode);
    elements.llmTopbarManual.classList.toggle('flex', !autoMode);
  }
  if (elements.llmTopbarAuto) {
    elements.llmTopbarAuto.classList.toggle('hidden', !autoMode);
    elements.llmTopbarAuto.classList.toggle('flex', autoMode);
  }
}

function updateStreamStatus(status) {
  if (!elements.streamStatus) return;

  const labels = {
    idle: 'Stream: idle',
    connecting: 'Stream: connecting',
    connected: 'Stream: connected',
    reconnecting: 'Stream: reconnecting',
    error: 'Stream: error',
    disconnected: 'Stream: disconnected',
  };

  elements.streamStatus.textContent = labels[status] || labels.idle;
  elements.streamStatus.className = `stream-status is-${status}`;
}

function setupMarked() {
  const renderer = new marked.Renderer();
  renderer.code = (code, language) => {
    const validLang = language || 'plaintext';
    let highlighted;
    try {
      highlighted = hljs.getLanguage(validLang) 
        ? hljs.highlight(code, { language: validLang }).value 
        : hljs.highlightAuto(code).value;
    } catch (e) {
      highlighted = escapeHtml(code);
    }

    return `
      <div class="relative group my-4 rounded-xl overflow-hidden border border-[#2a2a2a] bg-[#0d0d0d] font-mono">
        <div class="flex items-center justify-between px-4 py-2 bg-[#1a1a1a] border-b border-[#2a2a2a] text-[10px] font-bold text-zinc-500 uppercase tracking-widest">
          <span>${validLang}</span>
          <button type="button" class="flex items-center gap-1.5 hover:text-zinc-200 transition-colors copy-code-button" data-code="${encodeURIComponent(code)}">
            <i class="ph ph-copy text-sm"></i>
            <span>Copy Code</span>
          </button>
        </div>
        <pre class="p-4 overflow-x-auto custom-scrollbar text-sm leading-relaxed"><code class="hljs language-${validLang}">${highlighted}</code></pre>
      </div>
    `;
  };
  renderer.blockquote = (quote) => {
    const plain = extractPlainText(quote);
    if (!plain) {
      return '';
    }

    if (plain.includes('[!TOOL]')) {
      const metaText = plain.replace('[!TOOL]', '').trim();
      const meta = parseKeyValueLines(metaText);
      const tool = escapeHtml(meta.tool || 'unknown');
      const server = escapeHtml(meta.server || 'unknown');
      const phase = escapeHtml(meta.phase || 'running');
      const duration = escapeHtml(meta.duration || '');
      const args = escapeHtml(meta.arguments || '{}');
      const isRunning = /running|executing/i.test(phase);
      const openAttr = isRunning ? ' open' : '';
      const durationHtml = duration ? `<span>Duration: ${duration}</span>` : '';

      return `
        <div class="tool-live-card ${isRunning ? 'is-running' : 'is-done'}">
          <div class="tool-live-head">
            <span class="tool-live-title">Tool Call: ${tool}</span>
            <span class="tool-live-phase">${phase}${isRunning ? '<span class="loading-dots"><span></span><span></span><span></span></span>' : ''}</span>
          </div>
          <div class="tool-live-meta">
            <span>Server: ${server}</span>
            ${durationHtml}
          </div>
          <details class="tool-live-input" data-persist-key="tool-live-input"${openAttr}>
            <summary>Input Arguments</summary>
            <pre><code class="language-json">${args}</code></pre>
          </details>
        </div>
      `;
    }

    if (plain.includes('[!THOUGHT]')) {
      const text = plain.replace(/\[!THOUGHT\]/i, '').trim();
      return buildThoughtDropdownHtml([text], true);
    }

    return `<blockquote>${quote}</blockquote>`;
  };

  marked.setOptions({
    renderer,
    highlight: (code, lang) => {
      const language = hljs.getLanguage(lang) ? lang : 'plaintext';
      return hljs.highlight(code, { language }).value;
    },
    breaks: true,
    gfm: true,
  });
}

function extractPlainText(html) {
  return String(html)
    .replace(/<br\s*\/?>/gi, '\n')
    .replace(/<\/p>/gi, '\n')
    .replace(/<[^>]+>/g, '')
    .replace(/&nbsp;/g, ' ')
    .trim();
}

function parseKeyValueLines(text) {
  const result = {};
  text.split('\n').forEach((line) => {
    const i = line.indexOf(':');
    if (i <= 0) return;
    const key = line.slice(0, i).trim().toLowerCase();
    const value = line.slice(i + 1).trim();
    if (key) result[key] = value;
  });
  return result;
}

function parseAssistantThinking(rawContent) {
  let source = String(rawContent || '');

  // Parse multiple reasoning tag styles seen across Open WebUI + llama.cpp templates.
  const reasoningTags = [
    ['<think>', '</think>'],
    ['<thinking>', '</thinking>'],
    ['<reason>', '</reason>'],
    ['<reasoning>', '</reasoning>'],
    ['<thought>', '</thought>'],
    ['<Thought>', '</Thought>'],
    ['<|begin_of_thought|>', '<|end_of_thought|>'],
    ['◁think▷', '◁/think▷'],
  ];

  reasoningTags.forEach(([startTag, endTag]) => {
    const escapedStart = startTag.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const escapedEnd = endTag.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const pattern = new RegExp(`${escapedStart}([\\s\\S]*?)${escapedEnd}`, 'gi');
    source = source.replace(pattern, (_, block) => {
      const thought = String(block || '').trim();
      if (!thought) return '';
      return `\n\n> [!THOUGHT]\n${thought}\n\n`;
    });
  });

  // Open WebUI emits reasoning in details blocks for some providers.
  source = source.replace(/<details[^>]*type=["']reasoning["'][^>]*>([\s\S]*?)<\/details>/gi, (block, inner) => {
    const text = String(inner || '')
      .replace(/<summary>[\s\S]*?<\/summary>/gi, '')
      .replace(/<[^>]+>/g, '\n')
      .replace(/&nbsp;/g, ' ')
      .replace(/\n{3,}/g, '\n\n')
      .trim();
    if (!text) return '';
    return `\n\n> [!THOUGHT]\n${text}\n\n`;
  });

  const paragraphs = source.split(/\n\s*\n/).map((p) => p.trim()).filter(Boolean);
  const first = String(paragraphs[0] || '').toLowerCase();
  const internalPattern = /^(the user is asking|i should|i need to|i will|let me think|first, i|next, i|plan:|step 1|i can call|before i)/i;
  const userFacingPattern = /(i'd be happy to help|i can help you|let me know|what would you like|which machine|which tool|clarification)/i;
  
  if (first && internalPattern.test(first) && !userFacingPattern.test(first)) {
    const thoughtLine = paragraphs.shift();
    paragraphs.unshift(`> [!THOUGHT]\n${thoughtLine}`);
  }

  return { answer: paragraphs.join('\n\n') };
}

function getCachedThoughtsForMessage(messageId) {
  const key = String(messageId || '').trim();
  if (!key) return [];
  const cached = state.messageThoughtCache[key];
  return Array.isArray(cached) ? cached : [];
}

function cacheThoughtsForMessage(messageId, thoughts) {
  const key = String(messageId || '').trim();
  if (!key) return;
  if (!Array.isArray(thoughts) || thoughts.length === 0) return;
  state.messageThoughtCache[key] = thoughts.map((item) => String(item || '').trim()).filter(Boolean);
}

function buildThoughtDropdownHtml(thoughts, isOpen = false) {
  if (!Array.isArray(thoughts) || thoughts.length === 0) return '';
  const openAttr = isOpen ? ' open' : '';

  const mergedThoughts = thoughts
    .map((part) => String(part || '').trim())
    .filter(Boolean)
    .join('\n\n');
  const safeThought = escapeHtml(mergedThoughts);

  return `
    <details class="thought-details" data-persist-key="thought-main"${openAttr}>
      <summary>Thinking</summary>
      <div class="thought-content"><pre><code>${safeThought}</code></pre></div>
    </details>
  `;
}

function buildMessageFeedbackHtml(message) {
  const messageId = Number(message?.id || 0) || 0;
  if (!messageId || message?.role !== 'assistant') {
    return '';
  }

  const reaction = String(message.feedback_reaction || '').trim();
  const upActive = reaction === 'up' ? ' is-active' : '';
  const downActive = reaction === 'down' ? ' is-active' : '';

  return `
    <div class="message-feedback" data-message-id="${messageId}">
      <button type="button" class="message-feedback-button feedback-up${upActive}" onclick="handleMessageFeedback(${messageId}, 'up', this)" title="Helpful">
        <i class="ph ph-thumbs-up"></i>
      </button>
      <button type="button" class="message-feedback-button feedback-down${downActive}" onclick="handleMessageFeedback(${messageId}, 'down', this)" title="Not helpful">
        <i class="ph ph-thumbs-down"></i>
      </button>
      ${reaction ? `<button type="button" class="message-feedback-clear" onclick="handleMessageFeedback(${messageId}, 'clear', this)" title="Clear feedback">Clear</button>` : ''}
    </div>
  `;
}

function buildMessageKey(message, index) {
  const idPart = message && message.id !== undefined && message.id !== null ? String(message.id) : 'no-id';
  const tsPart = String((message && message.created_at) || 'no-ts');
  const rolePart = String((message && message.role) || 'unknown');
  return `${idPart}|${tsPart}|${rolePart}|${index}`;
}

function getDetailsStateKey(detailsEl, index) {
  const article = detailsEl.closest('.message');
  const messageKey = article ? (article.getAttribute('data-message-key') || 'unknown-message') : 'unknown-message';
  const explicitKey = detailsEl.getAttribute('data-persist-key');
  if (explicitKey) {
    return `${messageKey}::${explicitKey}`;
  }

  const summary = detailsEl.querySelector(':scope > summary');
  const summaryText = summary ? summary.textContent.trim() : '';
  return `${messageKey}::${detailsEl.className}::${summaryText}::${index}`;
}

function captureOpenDetailsState(container) {
  const openKeys = new Set();
  if (!container) return openKeys;

  const details = container.querySelectorAll('details');
  details.forEach((detailsEl, index) => {
    if (detailsEl.open) {
      openKeys.add(getDetailsStateKey(detailsEl, index));
    }
  });

  return openKeys;
}

function restoreOpenDetailsState(container, openKeys) {
  if (!container || !openKeys || openKeys.size === 0) return;

  const details = container.querySelectorAll('details');
  details.forEach((detailsEl, index) => {
    if (openKeys.has(getDetailsStateKey(detailsEl, index))) {
      detailsEl.open = true;
    }
  });
}

function suppressToolTraceNoise(html) {
  const wrapper = document.createElement('div');
  wrapper.innerHTML = String(html || '');

  wrapper.querySelectorAll('blockquote').forEach((node) => {
    const text = (node.textContent || '').replace(/\u00a0/g, ' ').trim();
    if (!text || /\[!TOOL\]/i.test(text)) {
      node.remove();
    }
  });

  return wrapper.innerHTML;
}

async function api(action, payload = {}) {
  const response = await fetch(`/api.php?action=${encodeURIComponent(action)}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return response.json();
}

function getStoredLlmSelection() {
  const providerId = Number(localStorage.getItem('llm-provider-id') || 0) || 0;
  const model = String(localStorage.getItem('llm-model') || '').trim();
  return { providerId, model };
}

function persistLlmSelection() {
  const providerId = Number(state.llmSelection.providerId || 0) || 0;
  const model = String(state.llmSelection.model || '').trim();

  if (providerId > 0) {
    localStorage.setItem('llm-provider-id', String(providerId));
  } else {
    localStorage.removeItem('llm-provider-id');
  }

  if (model) {
    localStorage.setItem('llm-model', model);
  } else {
    localStorage.removeItem('llm-model');
  }
}

function renderLlmProviderOptions() {
  if (!elements.llmProviderSelect) return;

  const providers = Array.isArray(state.llmProviders) ? state.llmProviders : [];
  const currentProviderId = Number(state.llmSelection.providerId || 0) || 0;

  if (providers.length === 0) {
    elements.llmProviderSelect.innerHTML = '<option value="0">No providers</option>';
    elements.llmProviderSelect.disabled = true;
    return;
  }

  elements.llmProviderSelect.disabled = false;
  elements.llmProviderSelect.innerHTML = providers.map((provider) => {
    const id = Number(provider.id || 0) || 0;
    const label = `${String(provider.name || 'Provider')} · ${String(provider.provider_type || 'openai-compatible')}`;
    const selected = id === currentProviderId ? ' selected' : '';
    return `<option value="${id}"${selected}>${escapeHtml(label)}</option>`;
  }).join('');
  renderRouteOptions();
  syncLlmTopbarMode();
}

function renderLlmModelControl(models = []) {
  const provider = state.llmProviders.find((item) => Number(item.id || 0) === Number(state.llmSelection.providerId || 0));
  const selectedModel = String(state.llmSelection.model || '').trim();

  if (elements.llmModelSelect) {
    elements.llmModelSelect.innerHTML = '';
    elements.llmModelSelect.disabled = false;
  }

  if (models.length > 0 && elements.llmModelSelect) {
    elements.llmModelSelect.classList.remove('hidden');
    if (elements.llmModelInput) {
      elements.llmModelInput.classList.add('hidden');
    }
    let options = models.map((entry) => {
      const modelName = String(entry?.name || '').trim();
      const loadedState = entry?.loaded === true ? 'loaded' : (entry?.loaded === false ? 'cold' : 'unknown');
      const loadedSuffix = loadedState === 'loaded' ? ' • loaded' : (loadedState === 'cold' ? ' • not loaded' : '');
      const selected = modelName === selectedModel ? ' selected' : '';
      return `<option value="${escapeHtml(modelName)}"${selected}>${escapeHtml(modelName + loadedSuffix)}</option>`;
    });

    if (selectedModel && !models.some((entry) => String(entry?.name || '').trim() === selectedModel)) {
      options.unshift(`<option value="${escapeHtml(selectedModel)}" selected>${escapeHtml(selectedModel)} (custom)</option>`);
    }

    elements.llmModelSelect.innerHTML = options.join('');
    if (!selectedModel && provider?.default_model) {
      elements.llmModelSelect.value = String(provider.default_model);
      state.llmSelection.model = String(provider.default_model);
    } else if (selectedModel) {
      elements.llmModelSelect.value = selectedModel;
    }
    refreshSelectedModelLoadedState();
    syncLlmTopbarMode();
    return;
  }

  if (elements.llmModelInput) {
    elements.llmModelInput.classList.remove('hidden');
    elements.llmModelInput.value = selectedModel || String(provider?.default_model || '');
    if (elements.llmModelSelect) {
      elements.llmModelSelect.classList.add('hidden');
    }
  }
  refreshSelectedModelLoadedState();
  syncLlmTopbarMode();
}

function setLlmSelection(providerId, model, options = {}) {
  const nextProviderId = Number(providerId || 0) || 0;
  const nextModel = String(model || '').trim();
  const persist = options.persist !== false;

  state.llmSelection.providerId = nextProviderId;
  state.llmSelection.model = nextModel;

  renderLlmProviderOptions();
  renderLlmModelControl(state.llmModels);
  syncLlmTopbarMode();

  if (persist) {
    persistLlmSelection();
  }
}

async function loadLlmModels(providerId, preferredModel = '') {
  const selectedProviderId = Number(providerId || 0) || 0;
  if (!selectedProviderId) {
    state.llmModels = [];
    renderLlmModelControl([]);
    return;
  }

  try {
    const data = await api('llm_models', { provider_id: selectedProviderId });
    state.llmModelsMeta = normalizeModelEntries(data.models || []);
    state.llmModels = state.llmModelsMeta;
    const provider = state.llmProviders.find((item) => Number(item.id || 0) === selectedProviderId);
    const nextModel = preferredModel || state.llmSelection.model || provider?.default_model || state.llmModelsMeta[0]?.name || '';
    state.llmSelection.providerId = selectedProviderId;
    state.llmSelection.model = String(nextModel || '').trim();
    updateAssistantLabel(state.llmSelection.model);
    renderLlmProviderOptions();
    renderLlmModelControl(state.llmModels);
    syncLlmTopbarMode();
    persistLlmSelection();
  } catch (error) {
    console.error('Failed to load LLM models', error);
    state.llmModels = [];
    state.llmModelsMeta = [];
    renderLlmModelControl([]);
  }
}

async function loadLlmProviders() {
  try {
    const data = await api('llm_providers');
    state.llmProviders = Array.isArray(data.providers) ? data.providers : [];
    const stored = getStoredLlmSelection();
    const hasStoredPreference = !!(stored.providerId || stored.model);

    const defaultProviderId = Number(data.default_provider_id || 0) || Number(chatShellRoot?.dataset?.defaultProviderId || 0) || 0;
    const selectedProvider = state.llmProviders.find((provider) => Number(provider.id || 0) === stored.providerId)
      || state.llmProviders.find((provider) => Number(provider.id || 0) === defaultProviderId)
      || state.llmProviders[0]
      || null;

    const nextProviderId = Number(selectedProvider?.id || 0) || 0;
    const nextModel = stored.model || selectedProvider?.default_model || data.legacy?.llm_model || '';
    
    state.llmSelection.providerId = nextProviderId;
    state.llmSelection.model = String(nextModel || '').trim();
    
    if (!hasStoredPreference) {
      state.llmRouteMode = 'auto';
      state.llmSelection.model = 'auto';
      state.llmSelection.providerId = getAutoRouterProviderId();
    } else {
      state.llmRouteMode = isRouterAutoSelection() ? 'auto' : `provider:${nextProviderId}`;
    }
    renderLlmProviderOptions();
    await loadLlmModels(nextProviderId, state.llmSelection.model);
    syncLlmTopbarMode();
    state.llmSelectionLoaded = true;
  } catch (error) {
    console.error('Failed to load LLM providers', error);
    state.llmProviders = [];
    state.llmModels = [];
    syncLlmTopbarMode();
    state.llmSelectionLoaded = true;
  }
}

async function handleRouteSelectionChange() {
  const value = String(elements.llmRouteSelect?.value || 'auto');
  state.llmRouteMode = value;

  if (value === 'auto') {
    const autoProviderId = getAutoRouterProviderId();
    state.llmSelection.providerId = autoProviderId;
    state.llmSelection.model = 'auto';
    updateAssistantLabel('Auto');
    persistLlmSelection();
    syncLlmTopbarMode();
    return;
  }

  const idMatch = value.match(/^provider:(\d+)$/);
  const providerId = idMatch ? Number(idMatch[1]) : 0;
  if (!providerId) {
    return;
  }
  const provider = state.llmProviders.find((item) => Number(item.id || 0) === providerId);
  state.llmSelection.providerId = providerId;
  state.llmSelection.model = String(provider?.default_model || '').trim();
  persistLlmSelection();
  await loadLlmModels(providerId, state.llmSelection.model);
}

async function handleProviderSelectionChange() {
  const providerId = Number(elements.llmProviderSelect?.value || 0) || 0;
  const provider = state.llmProviders.find((item) => Number(item.id || 0) === providerId);
  state.llmRouteMode = providerId > 0 ? `provider:${providerId}` : 'auto';
  state.llmSelection.providerId = providerId;
  state.llmSelection.model = String(provider?.default_model || '').trim();
  persistLlmSelection();
  await loadLlmModels(providerId, state.llmSelection.model);
}

function handleModelSelectionChange() {
  if (elements.llmModelSelect && !elements.llmModelSelect.classList.contains('hidden')) {
    state.llmSelection.model = String(elements.llmModelSelect.value || '').trim();
  } else if (elements.llmModelInput && !elements.llmModelInput.classList.contains('hidden')) {
    state.llmSelection.model = String(elements.llmModelInput.value || '').trim();
  }
  updateAssistantLabel(state.llmSelection.model);
  refreshSelectedModelLoadedState();
  syncLlmTopbarMode();
  persistLlmSelection();
}

async function submitMessageFeedback(messageId, reaction, buttonEl) {
  const conversationId = Number(state.conversationId || 0) || 0;
  const normalizedReaction = String(reaction || '').trim().toLowerCase();
  if (!conversationId || !messageId || !['up', 'down', 'clear'].includes(normalizedReaction)) {
    return;
  }

  if (buttonEl) {
    buttonEl.disabled = true;
  }

  try {
    await api('message_feedback', {
      conversation_id: conversationId,
      message_id: Number(messageId),
      reaction: normalizedReaction,
    });
    await loadMessages(conversationId);
  } catch (error) {
    console.error('Failed to save message feedback', error);
  } finally {
    if (buttonEl) {
      buttonEl.disabled = false;
    }
  }
}

function handleMessageFeedback(messageId, reaction, buttonEl) {
  submitMessageFeedback(messageId, reaction, buttonEl);
}

function renderClarificationBanner(clarifications) {
  if (!elements.clarificationBanner) return;

  if (!Array.isArray(clarifications) || clarifications.length === 0) {
    elements.clarificationBanner.innerHTML = '';
    elements.clarificationBanner.classList.add('hidden');
    updateInputState();
    return;
  }

  elements.clarificationBanner.classList.remove('hidden');
  elements.clarificationBanner.innerHTML = clarifications.map((request) => {
    const requestId = Number(request.id);
    const question = escapeHtml(String(request.question || 'What details are missing?'));
    
    let fieldsArray = [];
    let extraDetails = '';
    try {
      const parsed = JSON.parse(request.details_json || '{}');
      if (Array.isArray(parsed.fields)) {
        fieldsArray = parsed.fields;
      }
      if (parsed.details && parsed.details !== parsed.question) {
        extraDetails = parsed.details;
      }
    } catch(e) {}

    let fieldsHtml = '';
    if (fieldsArray.length > 0) {
      fieldsHtml = `
        <div class="clarification-form">
          ${fieldsArray.map((field, idx) => `
            <div class="clarification-field">
              <label class="clarification-label">${escapeHtml(field)}</label>
              <input type="text" class="clarification-input" data-field-name="${escapeHtml(field)}" placeholder="Enter ${escapeHtml(field).toLowerCase()}..." onkeydown="if(event.key==='Enter')handleClarificationAnswer(${requestId}, this.closest('.clarification-card').querySelector('.clarification-submit'))" />
            </div>
          `).join('')}
        </div>
      `;
    } else {
      fieldsHtml = `
        <div class="clarification-actions">
          <textarea class="clarification-answer" rows="3" placeholder="Type the missing information..."></textarea>
        </div>
      `;
    }

    return `
      <div class="clarification-card" data-clarification-id="${requestId}">
        <div class="clarification-head">
          <div class="clarification-icon"><i class="ph ph-question"></i></div>
          <div class="clarification-copy">
            <div class="clarification-title">More Information Needed</div>
            <div class="clarification-question">${question}</div>
            ${extraDetails ? `<div class="clarification-subtitle-details">${escapeHtml(extraDetails)}</div>` : ''}
          </div>
        </div>
        ${fieldsHtml}
        <div class="clarification-submit-wrapper">
          <button class="clarification-submit" type="button" onclick="handleClarificationAnswer(${requestId}, this)">Send Answer</button>
        </div>
      </div>
    `;
  }).join('');
  updateInputState();
}

async function submitClarificationAnswer(requestId, btnEl) {
  const card = document.querySelector(`.clarification-card[data-clarification-id="${requestId}"]`);
  if (!card) return;

  const inputs = card.querySelectorAll('.clarification-input');
  let answer = '';
  if (inputs.length > 0) {
    const parts = [];
    inputs.forEach((input) => {
      const fieldName = input.dataset.fieldName;
      const value = String(input.value || '').trim();
      if (value) {
        parts.push(`${fieldName}: ${value}`);
      }
    });
    answer = parts.join('\n');
  } else {
    const textarea = card.querySelector('.clarification-answer');
    answer = String(textarea?.value || '').trim();
  }

  if (!answer) return;

  btnEl.disabled = true;
  btnEl.textContent = 'Submitting...';
  try {
    await api('answer_clarification', { request_id: requestId, answer });
  } catch (error) {
    console.error('Failed to answer clarification', error);
    btnEl.disabled = false;
    btnEl.textContent = 'Send Answer';
  }
}

function handleClarificationAnswer(requestId, btnEl) {
  submitClarificationAnswer(requestId, btnEl);
}

function renderMessages(messages) {
  if (!elements.chatMessages) return;
  const container = elements.chatMessages;
  const shouldStick = state.stickToBottom && isNearBottom(container, 140);
  const previousDistanceFromBottom = container.scrollHeight - container.scrollTop;
  const openDetailsState = captureOpenDetailsState(container);

  if (!Array.isArray(messages) || messages.length === 0) {
    container.innerHTML = `
      <div class="empty-state m-auto max-w-md text-center py-20">
        <div class="w-16 h-16 rounded-2xl bg-[#2f2f2f] flex items-center justify-center mx-auto mb-6 shadow-sm">
          <i class="ph ph-chats-circle text-3xl text-zinc-400"></i>
        </div>
        <h3 class="text-xl font-semibold mb-2 text-zinc-200">How can I help you today?</h3>
        <p class="text-zinc-500 text-sm">Send the first prompt to enqueue a worker job.</p>
      </div>
    `;
    state.stickToBottom = true;
    return;
  }

  container.innerHTML = messages.map((message, index) => {
    const rawContent = String(message.content || '');
    const cleanContent = rawContent
      .replace(/Analyzing environment\. Found \d+ tools\.?/g, '')
      .replace(/Formulating plan\.{1,3}/g, '')
      .replace(/Generating response\.{1,3}/g, '')
      .replace(/Processing LLM response\.{1,3}/g, '')
      .trim();

    const isLatestMessage = index === messages.length - 1;

    // Suppress the redundant "Thinking..." placeholder message bubble when the loading dots are about to be shown below it.
    if (isLatestMessage && message.role === 'assistant' && cleanContent.toLowerCase() === 'thinking...' && state.isWaitingForLlm) {
      return '';
    }
    const perMessageModel = String(message?.llm_model || '').trim();
    const roleLabel = message.role === 'user'
      ? messageRoleLabels.user
      : (message.role === 'assistant' ? (perMessageModel || messageRoleLabels.assistant) : String(message.role || 'message'));

    const messageKey = escapeHtml(buildMessageKey(message, index));
    let contentHtml = '';

    if (message.role === 'user') {
      const parsedUserContent = parseAttachmentMessage(rawContent);
      if (parsedUserContent.attachments.length > 0) {
        const attachmentCards = parsedUserContent.attachments.map((attachment) => buildUserAttachmentCard(attachment)).join('');
        const textHtml = parsedUserContent.text ? `<div class="user-attachment-text">${escapeHtml(parsedUserContent.text)}</div>` : '';
        contentHtml = `${attachmentCards}${textHtml}`;
      } else {
        const plainText = parsedUserContent.text || cleanContent;
        contentHtml = escapeHtml(plainText);
      }
    } else {
      // Parse markdown for assistant messages and collapse model reasoning traces.
      const parsed = parseAssistantThinking(rawContent);
      const answerSource = parsed.answer
        .replace(/\[!TOOL\]/g, '');
      const rawHtml = marked.parse(answerSource || '');
      const answerHtml = suppressToolTraceNoise(DOMPurify.sanitize(rawHtml));
      const feedbackHtml = buildMessageFeedbackHtml(message);
      contentHtml = `${answerHtml}${feedbackHtml}`;
    }

    const bubbleClasses = message.role === 'user' 
      ? 'inline-flex w-auto max-w-[38rem] bg-[#2f2f2f] text-zinc-100 px-4 py-2.5 rounded-2xl rounded-tr-sm' 
      : 'flex flex-col w-auto max-w-[38rem] bg-[#1a1a1a] text-zinc-200 px-4 py-2.5 rounded-2xl rounded-tl-sm border border-[#2f2f2f] markdown-body prose prose-invert';

    return `
    <article class="flex w-full mb-6 ${message.role === 'user' ? 'justify-end' : 'justify-start'} message message-${message.role}" data-message-key="${messageKey}">
      <div class="flex w-full max-w-full ${message.role === 'user' ? 'flex-row-reverse' : 'flex-row'} gap-3 md:gap-4 group">
        
        <div class="w-8 h-8 rounded-full flex items-center justify-center shrink-0 ${message.role === 'user' ? 'bg-[#2f2f2f] text-zinc-300' : 'bg-[#1a1a1a] border border-[#3a3a3a] text-zinc-200'}">
          <i class="ph ${message.role === 'user' ? 'ph-user' : 'ph-robot'} text-xl"></i>
        </div>
        
        <div class="flex flex-col gap-1 ${message.role === 'user' ? 'items-end' : 'items-start'} min-w-0 w-full max-w-full">
          <div class="font-semibold text-xs text-zinc-500 capitalize tracking-wide px-1">${escapeHtml(roleLabel)}</div>
          <div class="message-body ${bubbleClasses} leading-relaxed break-words">${contentHtml}</div>
        </div>
        
      </div>
    </article>
  `;
  }).join('');
  
  const showLoadingBubble = Boolean(state.isWaitingForLlm);

  if (showLoadingBubble) {
    let loadingContent = `
      <div class="typing-indicator">
        <span></span>
        <span></span>
        <span></span>
      </div>
      ${state.llmLoadingHint ? `<div class="text-[11px] text-zinc-500 mt-1">${escapeHtml(state.llmLoadingHint)}</div>` : ''}
    `;

    if (state.nextScheduledAt !== null && state.nextScheduledAt > 0) {
      const diffMs = (state.nextScheduledAt * 1000) - Date.now();
      if (diffMs > 0) {
        loadingContent = `
          <div class="flex items-center gap-2 mb-1 mt-1">
            <i class="ph ph-clock text-zinc-400 text-lg"></i>
            <span class="scheduled-countdown text-[13px] font-mono tracking-wide text-zinc-300" data-target="${state.nextScheduledAt}">Evaluating schedule...</span>
          </div>
        `;
      }
    }

    container.innerHTML += `
      <article class="flex w-full mb-6 justify-start message message-assistant loading-message">
        <div class="flex w-full max-w-full flex-row gap-4 group">
          <div class="w-8 h-8 rounded-full flex items-center justify-center shrink-0 bg-[#1a1a1a] border border-[#3a3a3a] text-zinc-200">
            <i class="ph ph-robot text-xl"></i>
          </div>
          <div class="flex flex-col gap-1 items-start w-full max-w-full">
            <div class="font-semibold text-xs text-zinc-500 capitalize tracking-wide px-1">${escapeHtml(messageRoleLabels.assistant)}</div>
            <div class="message-body text-zinc-400 py-2">
              ${loadingContent}
            </div>
          </div>
        </div>
      </article>
    `;
  }

  container.dataset.loadedConversationId = String(state.conversationId || '');

  restoreOpenDetailsState(container, openDetailsState);

  if (shouldStick) {
    scrollChatToBottom();
  } else {
    container.scrollTop = Math.max(0, container.scrollHeight - previousDistanceFromBottom);
  }
}

function buildOptimisticUserMessageHtml(messageText, attachments) {
  const text = String(messageText || '').trim();
  const attachmentItems = Array.isArray(attachments) ? attachments : [];

  const attachmentHtml = attachmentItems.length > 0
    ? `
      <div class="mt-2 flex flex-wrap gap-2 text-[11px] text-zinc-400">
        ${attachmentItems.map((attachment) => {
          const name = escapeHtml(String(attachment?.name || 'attachment'));
          const size = formatFileSize(Number(attachment?.size || 0));
          return `<span class="px-2 py-1 rounded-full bg-[#2a2a2a] border border-[#3a3a3a]">${name} · ${size}</span>`;
        }).join('')}
      </div>
    `
    : '';

  const contentHtml = text
    ? escapeHtml(text)
    : '<span class="text-zinc-500 italic">Sending...</span>';

  return `
    <article class="flex w-full mb-6 justify-end message message-user" data-optimistic-message="1">
      <div class="flex w-full max-w-full flex-row-reverse gap-3 md:gap-4 group">
        <div class="w-8 h-8 rounded-full flex items-center justify-center shrink-0 bg-[#2f2f2f] text-zinc-300">
          <i class="ph ph-user text-xl"></i>
        </div>
        <div class="flex flex-col gap-1 items-end min-w-0 w-full max-w-full">
          <div class="font-semibold text-xs text-zinc-500 capitalize tracking-wide px-1">${escapeHtml(messageRoleLabels.user)}</div>
          <div class="message-body inline-flex w-auto max-w-[38rem] bg-[#2f2f2f] text-zinc-100 px-4 py-2.5 rounded-2xl rounded-tr-sm leading-relaxed break-words">
            <div>
              ${contentHtml}
              ${attachmentHtml}
            </div>
          </div>
        </div>
      </div>
    </article>
  `;
}

function buildOptimisticAssistantBubbleHtml(labelText = 'Sending...') {
  return `
    <article class="flex w-full mb-6 justify-start message message-assistant" data-optimistic-message="1" data-optimistic-role="assistant">
      <div class="flex w-full max-w-full flex-row gap-4 group">
        <div class="w-8 h-8 rounded-full flex items-center justify-center shrink-0 bg-[#1a1a1a] border border-[#3a3a3a] text-zinc-200">
          <i class="ph ph-robot text-xl"></i>
        </div>
        <div class="flex flex-col gap-1 items-start w-full max-w-full">
          <div class="font-semibold text-xs text-zinc-500 capitalize tracking-wide px-1">${escapeHtml(messageRoleLabels.assistant)}</div>
          <div class="message-body text-zinc-400 py-2">
            <div class="typing-indicator">
              <span></span>
              <span></span>
              <span></span>
            </div>
            <div class="text-[11px] text-zinc-500 mt-1">${escapeHtml(labelText)}</div>
          </div>
        </div>
      </div>
    </article>
  `;
}

function cacheConversationMessages(conversationId, messages) {
  const id = Number(conversationId || 0) || 0;
  if (!id) return;
  if (!Array.isArray(messages)) return;
  state.conversationMessageCache[id] = messages.map((message) => ({ ...message }));
}

function isNearBottom(container, threshold = 80) {
  const distance = container.scrollHeight - container.scrollTop - container.clientHeight;
  return distance <= threshold;
}

function isViewportNearBottom(threshold = 120) {
  const doc = document.documentElement;
  const scrollTop = window.scrollY || doc.scrollTop || 0;
  const viewportBottom = scrollTop + window.innerHeight;
  const totalHeight = Math.max(doc.scrollHeight, document.body?.scrollHeight || 0);
  return (totalHeight - viewportBottom) <= threshold;
}

function scrollChatToBottom() {
  if (!elements.chatMessages) return;
  const container = elements.chatMessages;
  const containerCanScroll = container.scrollHeight > (container.clientHeight + 1);

  // Two passes handle late layout changes from markdown/details blocks.
  requestAnimationFrame(() => {
    if (containerCanScroll) {
      container.scrollTop = container.scrollHeight;
    } else {
      window.scrollTo({ top: document.documentElement.scrollHeight, behavior: 'auto' });
    }
    setTimeout(() => {
      if (container.scrollHeight > (container.clientHeight + 1)) {
        container.scrollTop = container.scrollHeight;
      } else {
        window.scrollTo({ top: document.documentElement.scrollHeight, behavior: 'auto' });
      }
    }, 40);
  });
}

function bindChatScrollTracking() {
  if (!elements.chatMessages) return;
  elements.chatMessages.addEventListener('scroll', () => {
    state.stickToBottom = isNearBottom(elements.chatMessages);
  });

  window.addEventListener('scroll', () => {
    if (!elements.chatMessages) return;
    const containerCanScroll = elements.chatMessages.scrollHeight > (elements.chatMessages.clientHeight + 1);
    if (!containerCanScroll) {
      state.stickToBottom = isViewportNearBottom();
    }
  }, { passive: true });
}

function escapeHtml(value) {
  return String(value)
    .replace(/&(?!#?[a-zA-Z0-9]+;)/g, '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

function formatFileSize(bytes) {
  const size = Number(bytes) || 0;
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(size < 10 * 1024 ? 1 : 0)} KB`;
  return `${(size / (1024 * 1024)).toFixed(size < 10 * 1024 * 1024 ? 1 : 0)} MB`;
}

function isLikelyTextFile(file) {
  if (!file) return false;
  const type = String(file.type || '').toLowerCase();
  if (type.startsWith('text/')) return true;
  if (type === 'application/zip' || type === 'application/x-zip-compressed') return false;
  if (/(json|xml|javascript|x-javascript|ecmascript|yaml|yml|markdown|md|csv|sql|x-sh|shellscript)/.test(type)) return true;

  const name = String(file.name || '').toLowerCase();
  return /\.(txt|md|markdown|json|js|mjs|cjs|ts|tsx|jsx|py|php|html|htm|css|scss|sass|less|xml|yml|yaml|ini|conf|cfg|csv|tsv|sql|sh|bash|zsh|fish|rb|go|rs|java|kt|kts|c|h|cc|cpp|hpp|cs|toml|env|log|gitignore|dockerfile)$/i.test(name);
}

function readFileAsText(file) {
  return new Promise((resolve) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ''));
    reader.onerror = () => resolve('');
    reader.readAsText(file);
  });
}

function readFileAsBase64(file) {
  return new Promise((resolve) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = String(reader.result || '');
      const commaIndex = result.indexOf(',');
      resolve(commaIndex >= 0 ? result.slice(commaIndex + 1) : '');
    };
    reader.onerror = () => resolve('');
    reader.readAsDataURL(file);
  });
}

async function normalizeAttachment(file) {
  const textLike = isLikelyTextFile(file);
  const size = Number(file?.size || 0);
  const isZip = /\.(zip)$/i.test(String(file?.name || '')) || String(file?.type || '').toLowerCase() === 'application/zip';
  const meta = {
    name: String(file?.name || 'unnamed'),
    type: String(file?.type || 'application/octet-stream'),
    size,
    text: '',
    binary: !textLike,
    encoding: isZip ? 'base64' : 'text',
    truncated: false,
  };

  if (isZip) {
    const base64 = await readFileAsBase64(file);
    if (!base64) {
      meta.text = `[Unable to read archive contents: ${meta.name}]`;
      return meta;
    }
    meta.text = base64;
    return meta;
  }

  if (!textLike) {
    meta.text = `[Binary file omitted from inline preview: ${meta.name} (${meta.type || 'unknown type'}, ${formatFileSize(size)})]`;
    return meta;
  }

  const maxChars = 40000;
  const content = await readFileAsText(file);
  if (!content) {
    meta.text = `[Unable to read file contents: ${meta.name}]`;
    return meta;
  }

  if (content.length > maxChars) {
    meta.text = `${content.slice(0, maxChars)}\n\n[Truncated to ${formatFileSize(maxChars)} of ${formatFileSize(size)}]`;
    meta.truncated = true;
  } else {
    meta.text = content;
  }

  return meta;
}

function buildAttachmentMessage(messageText, attachments) {
  const parts = [];

  attachments.forEach((attachment) => {
    const header = `[ATTACHMENT name="${attachment.name}" type="${attachment.type}" size="${attachment.size}" encoding="${attachment.encoding || 'text'}"${attachment.binary ? ' binary="1"' : ''}${attachment.truncated ? ' truncated="1"' : ''}]`;
    const body = String(attachment.text || '').replace(/\r?\n/g, '\n');
    parts.push(`${header}\n${body}\n[/ATTACHMENT]`);
  });

  const text = String(messageText || '').trim();
  if (text) {
    parts.push(`[TEXT]\n${text}\n[/TEXT]`);
  }

  return parts.join('\n\n');
}

function stripTextTransportTags(rawValue) {
  return String(rawValue || '')
    .replace(/\[(?:\/?\s*text\s*|\s*text\s*\/)\]/gi, '')
    .trim();
}

function parseAttachmentMessage(rawContent) {
  const source = String(rawContent || '');
  const attachments = [];
  const attachmentPattern = /\[ATTACHMENT name="([^"]*)" type="([^"]*)" size="([^"]*)"(?: encoding="([^"]*)")?(?: binary="([^"]*)")?(?: truncated="([^"]*)")?\]\n([\s\S]*?)\n\[\/ATTACHMENT\]/g;

  let stripped = source.replace(attachmentPattern, (_, name, type, size, encoding, binary, truncated, content) => {
    attachments.push({
      name,
      type,
      size: Number(size) || 0,
      encoding: encoding || 'text',
      binary: binary === '1',
      truncated: truncated === '1',
      content,
    });
    return '\n';
  });

  let text = stripped
    .replace(/\[TEXT\]\s*\r?\n?([\s\S]*?)\r?\n?\s*\[\/TEXT\]/gi, (_, content) => content)
    .trim();

  if (!text) {
    text = stripTextTransportTags(stripped);
  }

  if (!text && /\[\s*text\s*\]/i.test(source)) {
    text = stripTextTransportTags(
      source.replace(/.*\[\s*text\s*\]\s*\r?\n?([\s\S]*?)\r?\n?\s*\[(?:\/\s*text\s*|\s*text\s*\/)\].*/is, '$1')
    );
  }

  return { attachments, text };
}

function buildUserAttachmentCard(attachment) {
  const content = String(attachment.content || '');
  const meta = [formatFileSize(attachment.size)];
  if (attachment.type) {
    meta.push(attachment.type);
  }
  if (attachment.encoding && attachment.encoding !== 'text') {
    meta.push(attachment.encoding);
  }
  if (attachment.truncated) {
    meta.push('truncated');
  }

  const isRenderableText = (attachment.encoding || 'text') === 'text' && !attachment.binary;
  const bodyHtml = isRenderableText ? `<pre><code>${escapeHtml(content)}</code></pre>` : '<div class="user-attachment-binary">Archive attached for deployment.</div>';

  return `
    <details class="user-attachment-card">
      <summary>
        <div class="user-attachment-summary-row">
          <span class="user-attachment-name">${escapeHtml(attachment.name)}</span>
          <span class="user-attachment-meta">${escapeHtml(meta.join(' · '))}</span>
        </div>
      </summary>
      ${bodyHtml}
    </details>
  `;
}

function clearPendingAttachments() {
  state.pendingAttachments = [];
  if (elements.fileInput) {
    elements.fileInput.value = '';
  }
  renderAttachmentDrafts();
  updateInputState();
}

function renderAttachmentDrafts() {
  if (!elements.attachmentList) return;

  const attachments = Array.isArray(state.pendingAttachments) ? state.pendingAttachments : [];
  if (attachments.length === 0) {
    elements.attachmentList.innerHTML = '';
    elements.attachmentList.classList.add('hidden');
    if (elements.attachmentButton) {
      elements.attachmentButton.title = 'Attach files';
    }
    return;
  }

  elements.attachmentList.classList.remove('hidden');
  elements.attachmentList.innerHTML = `
    <div class="composer-attachment-summary">
      <span>${attachments.length} file${attachments.length === 1 ? '' : 's'} attached</span>
      <button type="button" class="composer-attachment-clear" onclick="clearComposerAttachments()">Clear all</button>
    </div>
    <div class="composer-attachment-chips">
      ${attachments.map((attachment, index) => `
        <div class="composer-attachment-chip" title="${escapeHtml(attachment.name)}">
          <div class="composer-attachment-chip-meta">
            <i class="ph ph-file-text text-sm text-zinc-400"></i>
            <span class="composer-attachment-chip-name">${escapeHtml(attachment.name)}</span>
            <span class="composer-attachment-chip-size">${escapeHtml(formatFileSize(attachment.size))}</span>
          </div>
          <button type="button" class="composer-attachment-chip-remove" onclick="removeComposerAttachment(${index})" title="Remove attachment">
            <i class="ph ph-x"></i>
          </button>
        </div>
      `).join('')}
    </div>
  `;

  if (elements.attachmentButton) {
    elements.attachmentButton.title = `Attach files (${attachments.length} selected)`;
  }
}

function removeComposerAttachment(index) {
  state.pendingAttachments = state.pendingAttachments.filter((_, currentIndex) => currentIndex !== index);
  renderAttachmentDrafts();
  updateInputState();
}

function clearComposerAttachments() {
  clearPendingAttachments();
}

async function refreshConversationState(conversationId) {
  const selectedConversationId = Number(conversationId || 0) || 0;
  if (!selectedConversationId || state.conversationId !== selectedConversationId) {
    return;
  }

  try {
    const data = await api('conversation_state', { conversation_id: selectedConversationId });
    if (state.conversationId !== selectedConversationId) {
      return;
    }

    const wasWaitingForLlm = state.isWaitingForLlm;
    const pendingJobs = data.pending_jobs || 0;
    state.pendingJobsCount = Number(pendingJobs || 0) || 0;
    state.nextScheduledAt = data.next_scheduled_at || null;
    state.scheduledAutoApproveActive = Boolean(data.auto_approve_active);
    const nextMcpOverrideHash = String(data.mcp_override_hash || '');
    const mcpOverrideChanged = state.mcpOverrideHash !== nextMcpOverrideHash;
    state.mcpOverrideHash = nextMcpOverrideHash;
    applyAutoApproveState();

    if (data.llm_selection) {
      const selection = data.llm_selection || {};
      state.llmSelection.providerId = Number(selection.provider_id || 0) || 0;
      state.llmSelection.model = String(selection.model || '').trim();
      updateAssistantLabel(state.llmSelection.model);
      syncLlmTopbarMode();
      if (state.llmSelection.providerId > 0) {
        loadLlmModels(state.llmSelection.providerId, state.llmSelection.model).catch((e) => console.warn('Async loadLlmModels failed', e));
      }
    }

    state.isWaitingForLlm = pendingJobs > 0;
    if (wasWaitingForLlm !== state.isWaitingForLlm) {
      const cachedMessages = state.conversationMessageCache[selectedConversationId];
      if (Array.isArray(cachedMessages) && cachedMessages.length > 0) {
        renderMessages(cachedMessages);
      }
    }
    updateQueueStatus(pendingJobs);
    if (pendingJobs <= 0) {
      stopModelLoadWatcher();
    }
    updateInputState();

    if (mcpOverrideChanged) {
      loadMcpServers();
    }

    if (Array.isArray(data.pending_clarifications)) {
      state.pendingClarifications = data.pending_clarifications || [];
      renderClarificationBanner(state.pendingClarifications);
    }

    if (Array.isArray(data.pending_approvals)) {
      state.pendingApprovals = data.pending_approvals || [];
      renderApprovalBanner(state.pendingApprovals);
    }
  } catch (error) {
    console.warn('Failed to refresh conversation state', error);
  }
}

async function loadMessages(conversationId, options = {}) {
  // If we are on a page without chat messages (like scheduled.php),
  // clicking a conversation should just redirect back to index.php or do nothing.
  if (!elements.chatMessages) {
    window.location.href = `/?conversation_id=${conversationId}`;
    return;
  }

  const selectedConversationId = Number(conversationId);
  if (!Number.isFinite(selectedConversationId) || selectedConversationId <= 0) {
    return;
  }

  const previousConversationId = state.conversationId;
  state.loadingConversationId = selectedConversationId;
  state.conversationId = selectedConversationId;
  document.querySelectorAll('.conversation-item').forEach((item) => {
    item.classList.toggle('is-active', Number(item.dataset.id) === state.conversationId);
  });

  const cachedMessages = state.conversationMessageCache[selectedConversationId];
  if (previousConversationId !== selectedConversationId && elements.chatMessages) {
    if (Array.isArray(cachedMessages) && cachedMessages.length > 0) {
      renderMessages(cachedMessages);
    } else {
      elements.chatMessages.innerHTML = `
        <div class="empty-state m-auto max-w-md text-center py-20">
          <div class="w-16 h-16 rounded-2xl bg-[#2f2f2f] flex items-center justify-center mx-auto mb-6 shadow-sm">
            <i class="ph ph-circle-notch animate-spin text-3xl text-zinc-400"></i>
          </div>
          <h3 class="text-xl font-semibold mb-2 text-zinc-200">Loading conversation</h3>
          <p class="text-zinc-500 text-sm">Fetching chat history and state…</p>
        </div>
      `;
    }
  }

  try {
    // Instrumentation: measure network time for messages API and client render time.
    const networkStartedAt = performance.now();
    const data = await api('messages', { conversation_id: state.conversationId, light: true });
    console.log(`messages:network:${selectedConversationId}: ${performance.now() - networkStartedAt} ms`);
    if (state.loadingConversationId !== selectedConversationId) {
      return;
    }
  const renderStartedAt = performance.now();
    renderMessages(data.messages || []);
  console.log(`messages:render:${selectedConversationId}: ${performance.now() - renderStartedAt} ms`);
    cacheConversationMessages(selectedConversationId, data.messages || []);
    state.stickToBottom = true;
    scrollChatToBottom();

    const url = new URL(window.location.href);
    url.searchParams.set('conversation_id', String(state.conversationId));
    window.history.replaceState({}, '', `${url.pathname}${url.search}`);

    if (!options.skipStateRefresh) {
      void refreshConversationState(selectedConversationId);
    }

    // Keep SSE active for the selected conversation so updates appear without refresh.
    startSSE();
  } catch (error) {
    console.error('Failed to load conversation messages', error);
  } finally {
    if (state.loadingConversationId === selectedConversationId) {
      state.loadingConversationId = null;
    }
  }
}

async function pollMessagesOnce() {
  if (!state.conversationId || state.pollInFlight || state.sseConnected) {
    return;
  }

  state.pollInFlight = true;
  try {
    const data = await api('messages', { conversation_id: state.conversationId });
    const pendingJobs = data.pending_jobs || 0;
    state.pendingJobsCount = Number(pendingJobs || 0) || 0;
    state.nextScheduledAt = data.next_scheduled_at || null;
    state.scheduledAutoApproveActive = Boolean(data.auto_approve_active);
    const nextMcpOverrideHash = String(data.mcp_override_hash || '');
    const mcpOverrideChanged = state.mcpOverrideHash !== nextMcpOverrideHash;
    state.mcpOverrideHash = nextMcpOverrideHash;
    applyAutoApproveState();

    state.isWaitingForLlm = pendingJobs > 0;
    if (Array.isArray(data.messages)) {
      renderMessages(data.messages);
    }
    updateQueueStatus(pendingJobs);
    updateInputState();
    if (mcpOverrideChanged) {
      loadMcpServers();
    }
    if (Array.isArray(data.pending_clarifications)) {
      state.pendingClarifications = data.pending_clarifications || [];
      renderClarificationBanner(state.pendingClarifications);
    }
  } catch (error) {
    console.warn('Polling fallback failed', error);
  } finally {
    state.pollInFlight = false;
  }
}

function ensurePollingFallback() {
  if (state.pollIntervalId) return;
  state.pollIntervalId = window.setInterval(pollMessagesOnce, 2500);
}

function stopPollingFallback() {
  if (!state.pollIntervalId) return;
  window.clearInterval(state.pollIntervalId);
  state.pollIntervalId = null;
}

function updateQueueStatus(pendingJobs) {
  if (elements.queueStatus) {
    const hasExplicit = pendingJobs !== undefined && pendingJobs !== null;
    const count = hasExplicit ? (Number(pendingJobs || 0) || 0) : (Number(state.pendingJobsCount || 0) || 0);
    if (count > 0) {
      const loadingSuffix = state.llmLoadingHint ? ` · ${state.llmLoadingHint}` : '';
      elements.queueStatus.textContent = `Queue: ${count} job(s)${loadingSuffix}`;
      return;
    }
    elements.queueStatus.textContent = 'Idle';
  }
}

function applyAutoApproveState() {
  const effective = Boolean(state.manualAutoApprove || state.scheduledAutoApproveActive);
  state.autoApprove = effective;

  const hint = state.scheduledAutoApproveActive
    ? 'Scheduled prompt auto-approve is active for this conversation'
    : 'Auto-approve all tool calls without confirmation';

  if (elements.autoApproveToggle) {
    elements.autoApproveToggle.checked = effective;
    elements.autoApproveToggle.title = hint;
  }

  if (elements.autoApproveToggleLabel) {
    elements.autoApproveToggleLabel.title = hint;
  }
}

// ============================================================
// Tool Approval Banner
// ============================================================

async function approveToolCall(approvalId, btnEl) {
  btnEl.disabled = true;
  try {
    await api('approve_tool', { approval_id: approvalId });
  } catch (e) {
    console.error('Failed to approve tool call', e);
    btnEl.disabled = false;
  }
}

async function denyToolCall(approvalId, btnEl) {
  btnEl.disabled = true;
  try {
    await api('deny_tool', { approval_id: approvalId });
  } catch (e) {
    console.error('Failed to deny tool call', e);
    btnEl.disabled = false;
  }
}

function renderApprovalBanner(approvals) {
  if (!elements.toolApprovalBanner) return;

  if (!Array.isArray(approvals) || approvals.length === 0) {
    elements.toolApprovalBanner.innerHTML = '';
    elements.toolApprovalBanner.classList.add('hidden');
    return;
  }

  // Auto-approve: fire API for each pending approval automatically
  if (state.autoApprove) {
    approvals.forEach(async (approval) => {
      try {
        await api('approve_tool', { approval_id: Number(approval.id) });
      } catch (e) {
        console.error('Auto-approve failed', e);
      }
    });
    elements.toolApprovalBanner.innerHTML = '';
    elements.toolApprovalBanner.classList.add('hidden');
    return;
  }

  elements.toolApprovalBanner.classList.remove('hidden');
  elements.toolApprovalBanner.innerHTML = approvals.map((approval) => {
    let argsHtml = '';
    try {
      const args = JSON.parse(approval.arguments_json || '{}');
      argsHtml = escapeHtml(JSON.stringify(args, null, 2));
    } catch {
      argsHtml = escapeHtml(approval.arguments_json || '{}');
    }
    const toolName = escapeHtml(approval.tool_name || 'unknown');
    const serverName = escapeHtml(approval.server_name || 'unknown');
    const approvalId = Number(approval.id);

    return `
      <div class="tool-approval-card" data-approval-id="${approvalId}">
        <div class="tool-approval-header">
          <div class="tool-approval-icon">
            <i class="ph ph-shield-warning"></i>
          </div>
          <div class="tool-approval-info">
            <div class="tool-approval-title">Tool Execution Requires Approval</div>
            <div class="tool-approval-meta">
              <span class="tool-approval-badge tool">${toolName}</span>
              <span class="tool-approval-badge server">via ${serverName}</span>
            </div>
          </div>
        </div>
        <details class="tool-approval-args">
          <summary>View Arguments</summary>
          <pre><code class="language-json">${argsHtml}</code></pre>
        </details>
        <div class="tool-approval-actions">
          <button class="tool-approval-deny" onclick="handleDenyApproval(${approvalId}, this)">
            <i class="ph ph-x"></i> Deny
          </button>
          <button class="tool-approval-approve" onclick="handleApproveApproval(${approvalId}, this)">
            <i class="ph ph-check"></i> Approve
          </button>
        </div>
      </div>
    `;
  }).join('');
}

function handleApproveApproval(approvalId, btnEl) {
  const card = document.querySelector(`.tool-approval-card[data-approval-id="${approvalId}"]`);
  if (card) {
    card.style.opacity = '0.5';
    card.style.pointerEvents = 'none';
  }
  approveToolCall(approvalId, btnEl);
}

function handleDenyApproval(approvalId, btnEl) {
  const card = document.querySelector(`.tool-approval-card[data-approval-id="${approvalId}"]`);
  if (card) {
    card.style.opacity = '0.5';
    card.style.pointerEvents = 'none';
  }
  denyToolCall(approvalId, btnEl);
}

function initAutoApproveToggle() {
  if (!elements.autoApproveToggle) return;
  state.manualAutoApprove = false;
  state.scheduledAutoApproveActive = false;
  applyAutoApproveState();

  elements.autoApproveToggle.addEventListener('change', () => {
    state.manualAutoApprove = elements.autoApproveToggle.checked;
    applyAutoApproveState();
    if (state.autoApprove && state.pendingApprovals.length > 0) {
      renderApprovalBanner(state.pendingApprovals);
    }
  });

  elements.llmProviderSelect?.addEventListener('change', () => {
    handleProviderSelectionChange();
  });

  elements.llmRouteSelect?.addEventListener('change', () => {
    handleRouteSelectionChange();
  });

  elements.llmModelSelect?.addEventListener('change', () => {
    handleModelSelectionChange();
  });

  elements.llmModelInput?.addEventListener('input', () => {
    handleModelSelectionChange();
  });
}


function updateInputState() {
  if (!elements.sendButton || !elements.messageInput) return;
  const icon = elements.sendButton.querySelector('i');
  const runBusy = state.isWaitingForLlm;
  const blocked = runBusy || state.isPreparingAttachments || (state.pendingClarifications?.length || 0) > 0;
  if (blocked) {
    elements.sendButton.disabled = runBusy ? state.isTerminatingRun : true;
    elements.messageInput.disabled = true;
    if (runBusy) {
      if (icon) icon.className = 'ph-fill ph-stop text-lg';
      elements.sendButton.title = 'Stop current run';
    } else {
      if (icon) icon.className = 'ph ph-circle-notch animate-spin text-lg';
      elements.sendButton.title = 'Working...';
    }
  } else {
    const hasDraftContent = Boolean(elements.messageInput.value.trim()) || (state.pendingAttachments?.length || 0) > 0;
    elements.sendButton.disabled = !hasDraftContent;
    elements.messageInput.disabled = false;
    if (icon) icon.className = 'ph-fill ph-paper-plane-right text-lg';
    elements.sendButton.title = 'Send message';
    elements.messageInput.focus();
  }

  if (elements.attachmentButton) {
    elements.attachmentButton.disabled = blocked;
  }
}

async function terminateCurrentRun() {
  if (!state.conversationId || state.isTerminatingRun) {
    return;
  }

  state.isTerminatingRun = true;
  updateInputState();
  try {
    const data = await api('terminate_run', { conversation_id: state.conversationId });
    state.isWaitingForLlm = Number(data?.pending_jobs || 0) > 0;
    updateQueueStatus(Number(data?.pending_jobs || 0));
    await loadMessages(state.conversationId);
  } catch (error) {
    console.error('Failed to terminate run', error);
  } finally {
    state.isTerminatingRun = false;
    updateInputState();
  }
}

function autoResizeInput() {
  if (!elements.messageInput) return;
  const maxHeight = 160;
  elements.messageInput.style.height = 'auto';
  const nextHeight = Math.min(elements.messageInput.scrollHeight, maxHeight);
  elements.messageInput.style.height = `${nextHeight}px`;
  elements.messageInput.style.overflowY = elements.messageInput.scrollHeight > maxHeight ? 'auto' : 'hidden';
}

function stopSSE() {
  if (state.sseSource) {
    state.sseManuallyClosed = true;
    state.sseSource.close();
    state.sseSource = null;
  }
  state.sseConnected = false;
  ensurePollingFallback();
  updateStreamStatus('disconnected');
}

function startSSE() {
  stopSSE();
  if (!state.conversationId || !elements.chatMessages) return;

  state.sseManuallyClosed = false;
  updateStreamStatus('connecting');
  state.sseSource = new EventSource(`/sse.php?conversation_id=${state.conversationId}`);

  state.sseSource.onopen = () => {
    state.sseConnected = true;
    stopPollingFallback();
    updateStreamStatus('connected');
  };
  
  state.sseSource.onmessage = (event) => {
    let data;
    try {
      data = JSON.parse(event.data);
    } catch (error) {
      console.warn('Invalid SSE payload', error);
      return;
    }

    if (data.connected) {
      updateStreamStatus('connected');
    }
    
    // Ignore internal connection/ping events without message payload
    if (data.connected && data.messages === undefined) return;

    const pendingJobs = data.pending_jobs || 0;
    state.pendingJobsCount = Number(pendingJobs || 0) || 0;
    if (data.next_scheduled_at !== undefined) {
      state.nextScheduledAt = data.next_scheduled_at;
    }
    if (data.auto_approve_active !== undefined) {
      state.scheduledAutoApproveActive = Boolean(data.auto_approve_active);
      applyAutoApproveState();
    }
    if (data.mcp_override_hash !== undefined) {
      const nextMcpOverrideHash = String(data.mcp_override_hash || '');
      if (state.mcpOverrideHash !== nextMcpOverrideHash) {
        state.mcpOverrideHash = nextMcpOverrideHash;
        loadMcpServers();
      }
    }

    if (data.pending_clarifications !== undefined) {
      state.pendingClarifications = data.pending_clarifications || [];
      renderClarificationBanner(state.pendingClarifications);
    }

    // Always sync UI state from SSE payload, even if only pending status changed.
    state.isWaitingForLlm = pendingJobs > 0;
    if (Array.isArray(data.messages)) {
      renderMessages(data.messages);
    }
    updateQueueStatus(pendingJobs);
    updateInputState();

    // Sync tool approvals from SSE if present
    if (data.pending_approvals !== undefined) {
      state.pendingApprovals = data.pending_approvals || [];
      renderApprovalBanner(state.pendingApprovals);
    }
  };
  
  state.sseSource.onerror = (err) => {
    console.warn("SSE error, retrying...", err);
    state.sseConnected = false;
    ensurePollingFallback();
    if (!state.sseManuallyClosed) {
      updateStreamStatus('reconnecting');
    }
    // EventSource auto-reconnects natively.
  };
}

async function createConversation(options = {}) {
  if (state.isCreatingConversation) return null;
  state.isCreatingConversation = true;
  try {
    const result = await api('new_conversation');
    const id = Number(result?.id || 0);
    if (!id) return null;

    if (options.redirect !== false) {
      window.location.href = `/?conversation_id=${id}`;
      return id;
    }

    return id;
  } finally {
    state.isCreatingConversation = false;
  }
}

async function loadMcpServers() {
  if (!elements.mcpServerList) return;
  try {
    const payload = {};
    if (state.conversationId) {
      payload.conversation_id = state.conversationId;
    }
    const data = await api('mcp_servers', payload);
    renderMcpServers(data.servers || []);
  } catch (error) {
    console.error('Failed to load MCP servers', error);
    elements.mcpServerList.innerHTML = '<div class="sidebar-mcp-loading">Error loading tools.</div>';
  }
}

function renderMcpServers(servers) {
  if (!elements.mcpServerList) return;
  if (servers.length === 0) {
    elements.mcpServerList.innerHTML = '<div class="sidebar-mcp-loading">No tools found.</div>';
    return;
  }

  elements.mcpServerList.innerHTML = servers.map(server => `
    <div class="mcp-server-item">
      <span class="mcp-server-name" title="${escapeHtml(server.name)}">${escapeHtml(server.name)}</span>
      <label class="switch">
        <input type="checkbox" ${server.effective_is_active ? 'checked' : ''} ${server.forced_by_schedule ? 'disabled' : ''} data-id="${server.id}" onchange="toggleMcpServer(this)" title="${server.forced_by_schedule ? 'Controlled by active scheduled prompt settings' : 'Toggle server availability'}">
        <span class="slider"></span>
      </label>
    </div>
  `).join('');
}

async function toggleMcpServer(checkbox) {
  const id = Number(checkbox.dataset.id);
  const isActive = checkbox.checked;
  checkbox.disabled = true;
  try {
    await api('toggle_mcp_server', { id, is_active: isActive });
  } catch (error) {
    console.error('Failed to toggle MCP server', error);
    checkbox.checked = !isActive; // Rollback
    alert('Failed to update tool status.');
  } finally {
    checkbox.disabled = false;
  }
}

async function loadProfile() {
  if (!elements.personaInput || !elements.blueprintsInput) return;
  try {
    const data = await api('get_profile');
    if (data.profile) {
      elements.personaInput.value = data.profile.persona || '';
      elements.blueprintsInput.value = data.profile.blueprints || '';
      updateIdentityPreviews();
    }
  } catch (error) {
    console.error('Failed to load profile', error);
  }
}

function updateIdentityPreviews() {
  if (elements.personaPreview) {
    const val = elements.personaInput.value.trim();
    elements.personaPreview.textContent = val ? (val.split('\n')[0].substring(0, 40) + (val.length > 40 ? '...' : '')) : 'No persona active';
  }
  if (elements.blueprintsPreview) {
    const val = elements.blueprintsInput.value.trim();
    elements.blueprintsPreview.textContent = val ? (val.split('\n')[0].substring(0, 40) + (val.length > 40 ? '...' : '')) : 'No infrastructure active';
  }
}

async function saveProfile() {
  if (!elements.saveIdentityButton) return;
  
  const persona = elements.personaInput.value.trim();
  const blueprints = elements.blueprintsInput.value.trim();
  
  elements.saveIdentityButton.disabled = true;
  elements.saveIdentityButton.textContent = 'Saving...';
  
  try {
    await api('update_profile', { persona, blueprints });
    updateIdentityPreviews();
    elements.saveIdentityButton.textContent = 'Identity Saved! ✓';
    setTimeout(() => {
      elements.saveIdentityButton.textContent = 'Save Identity';
      elements.saveIdentityButton.disabled = false;
    }, 2000);
  } catch (error) {
    console.error('Failed to save profile', error);
    alert('Failed to save identity.');
    elements.saveIdentityButton.textContent = 'Save Identity';
    elements.saveIdentityButton.disabled = false;
  }
}

async function deleteConversation(conversationId) {
  if (!confirm('Delete this conversation?')) return;
  await api('delete_conversation', { conversation_id: conversationId });
  const btn = document.querySelector(`.conversation-delete[data-id="${conversationId}"]`);
  if (btn) btn.closest('.group')?.remove();
  
  if (state.conversationId === conversationId) {
    state.conversationId = null;
    if (elements.chatMessages) {
      elements.chatMessages.innerHTML = `
        <div class="empty-state">
          <h3>Conversation deleted</h3>
          <p>Select or create a conversation.</p>
        </div>
      `;
    }
    stopSSE();
    updateQueueStatus(0);
    updateInputState();
  }
}

async function sendMessage() {
  const message = elements.messageInput?.value.trim();
  const attachments = Array.isArray(state.pendingAttachments) ? state.pendingAttachments : [];
  if ((!message && attachments.length === 0) || state.isWaitingForLlm || state.isPreparingAttachments) {
    return;
  }

  let createdConversationNow = false;
  const combinedMessage = buildAttachmentMessage(message, attachments);
  const draftMessage = message;
  const draftAttachments = attachments.slice();

  elements.messageInput.value = '';
  elements.messageInput.style.height = 'auto'; // Reset size
  elements.messageInput.style.overflowY = 'hidden';
  clearPendingAttachments();

  // Show the user's prompt immediately, even if we still need to create the conversation first.
  if (elements.chatMessages) {
    elements.chatMessages.insertAdjacentHTML('beforeend', buildOptimisticUserMessageHtml(draftMessage, draftAttachments));
    elements.chatMessages.insertAdjacentHTML('beforeend', buildOptimisticAssistantBubbleHtml('Preparing your reply…'));
    scrollChatToBottom();
  }

  if (!state.conversationId) {
    const createdId = await createConversation({ redirect: false });
    if (!createdId) {
      // Restore draft if conversation creation failed.
      elements.chatMessages?.querySelectorAll('[data-optimistic-message="1"]').forEach((node) => node.remove());
      elements.messageInput.value = draftMessage;
      state.pendingAttachments = draftAttachments;
      renderAttachmentDrafts();
      autoResizeInput();
      updateInputState();
      return;
    }
    state.conversationId = createdId;
    createdConversationNow = true;
  }
  
  // Optimistically set waiting state
  state.isWaitingForLlm = true;
  updateInputState();
  
  try {
    const enqueueTimerLabel = `enqueue:${state.conversationId}`;
    if (state.llmRouteMode === 'auto') {
      const autoProviderId = getAutoRouterProviderId();
      state.llmSelection.providerId = autoProviderId;
      state.llmSelection.model = 'auto';
    }

    if (state.llmRouteMode !== 'auto' && state.llmSelectedModelLoaded === false) {
      state.llmLoadingHint = `Loading model into memory: ${state.llmSelection.model}`;
      startModelLoadWatcher();
    }

    console.time(enqueueTimerLabel);
    await api('enqueue', {
      conversation_id: state.conversationId,
      message: combinedMessage,
      llm_provider_id: Number(state.llmSelection.providerId || 0) || 0,
      llm_model: String(state.llmSelection.model || '').trim(),
    });
    console.timeEnd(enqueueTimerLabel);
  } catch (error) {
    console.error('Failed to enqueue message', error);
    elements.chatMessages?.querySelectorAll('[data-optimistic-message="1"]').forEach((node) => node.remove());
    elements.messageInput.value = draftMessage;
    state.pendingAttachments = draftAttachments;
    renderAttachmentDrafts();
    autoResizeInput();
    state.isWaitingForLlm = false;
    updateInputState();
    return;
  }

  if (createdConversationNow) {
    window.location.href = `/?conversation_id=${state.conversationId}`;
    return;
  }

  loadMessages(state.conversationId, { skipStateRefresh: true });
  void refreshConversationState(state.conversationId);
}

function bindEvents() {
  elements.conversationList?.addEventListener('click', (event) => {
    const deleteButton = event.target.closest('.conversation-delete');
    if (deleteButton) {
      event.preventDefault();
      event.stopPropagation();
      deleteConversation(Number(deleteButton.dataset.id));
      return;
    }

    const conversationButton = event.target.closest('.conversation-item');
    if (conversationButton) {
      event.preventDefault();
      loadMessages(conversationButton.dataset.id);
    }
  });

  elements.newConversationButton?.addEventListener('click', (event) => {
    event.preventDefault();
    createConversation();
  });
  elements.saveIdentityButton?.addEventListener('click', saveProfile);
  elements.sendButton?.addEventListener('click', () => {
    if (state.isWaitingForLlm) {
      terminateCurrentRun();
      return;
    }
    sendMessage();
  });
  elements.attachmentButton?.addEventListener('click', () => {
    if (elements.fileInput && !state.isWaitingForLlm && !state.isPreparingAttachments) {
      elements.fileInput.click();
    }
  });
  elements.fileInput?.addEventListener('change', async (event) => {
    const files = Array.from(event.target.files || []);
    if (files.length === 0) return;

    state.isPreparingAttachments = true;
    updateInputState();

    try {
      const normalized = await Promise.all(files.map((file) => normalizeAttachment(file)));
      state.pendingAttachments = normalized;
      renderAttachmentDrafts();
    } finally {
      state.isPreparingAttachments = false;
      updateInputState();
    }
  });
  elements.messageInput?.addEventListener('input', () => {
    autoResizeInput();
    updateInputState();
  });
  elements.messageInput?.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      sendMessage();
    }
  });

  elements.sidebarToggle?.addEventListener('click', () => {
    const body = document.body;
    body.classList.toggle('sidebar-hidden');
    localStorage.setItem('sidebar-hidden', body.classList.contains('sidebar-hidden') ? '1' : '0');
  });

  // Identity File Uploads
  elements.personaUploadLink?.addEventListener('click', (e) => {
    e.preventDefault();
    elements.personaFileInput?.click();
  });

  elements.infrastructureUploadLink?.addEventListener('click', (e) => {
    e.preventDefault();
    elements.infrastructureFileInput?.click();
  });

  elements.personaFileInput?.addEventListener('change', (e) => {
    const file = e.target.files[0];
    if (file) {
      const reader = new FileReader();
      reader.onload = (event) => {
        if (elements.personaInput) {
          elements.personaInput.value = event.target.result;
          saveProfile();
        }
      };
      reader.readAsText(file);
    }
  });

  elements.infrastructureFileInput?.addEventListener('change', (e) => {
    const file = e.target.files[0];
    if (file) {
      const reader = new FileReader();
      reader.onload = (event) => {
        if (elements.blueprintsInput) {
          elements.blueprintsInput.value = event.target.result;
          saveProfile();
        }
      };
      reader.readAsText(file);
    }
  });

  // Dialog handling
  let currentDialogTarget = null;
  elements.viewPersonaLink?.addEventListener('click', (e) => {
    e.preventDefault();
    if (!elements.identityDialog || !elements.personaInput) return;
    currentDialogTarget = 'persona';
    elements.dialogTitle.textContent = 'Edit AI Persona';
    elements.dialogTextarea.value = elements.personaInput.value;
    elements.identityDialog.showModal();
  });

  elements.viewInfrastructureLink?.addEventListener('click', (e) => {
    e.preventDefault();
    if (!elements.identityDialog || !elements.blueprintsInput) return;
    currentDialogTarget = 'blueprints';
    elements.dialogTitle.textContent = 'Edit Infrastructure';
    elements.dialogTextarea.value = elements.blueprintsInput.value;
    elements.identityDialog.showModal();
  });

  elements.closeDialogButton?.addEventListener('click', () => {
    elements.identityDialog.close();
  });

  elements.saveDialogButton?.addEventListener('click', () => {
    if (currentDialogTarget === 'persona' && elements.personaInput) {
      elements.personaInput.value = elements.dialogTextarea.value;
    } else if (currentDialogTarget === 'blueprints' && elements.blueprintsInput) {
      elements.blueprintsInput.value = elements.dialogTextarea.value;
    }
    saveProfile();
    elements.identityDialog.close();
  });

  elements.identityDialog?.addEventListener('click', (e) => {
    if (e.target === elements.identityDialog) elements.identityDialog.close();
  });

  // Global Code Copy Listener
  document.addEventListener('click', (e) => {
    const btn = e.target.closest('.copy-code-button');
    if (!btn) return;
    
    const code = decodeURIComponent(btn.dataset.code);
    navigator.clipboard.writeText(code).then(() => {
      const original = btn.innerHTML;
      btn.innerHTML = '<i class="ph ph-check text-green-400"></i><span class="text-green-400">Copied!</span>';
      setTimeout(() => {
        btn.innerHTML = original;
      }, 2000);
    }).catch(err => {
      console.error('Failed to copy code: ', err);
    });
  });
}

async function initialize() {
  setupMarked();
  updateStreamStatus('idle');
  ensurePollingFallback();
  initAutoApproveToggle();

  // Restore sidebar state
  if (localStorage.getItem('sidebar-hidden') === '1') {
    document.body.classList.add('sidebar-hidden');
  }

  bindEvents();
  bindChatScrollTracking();
  if (elements.messageInput) {
    elements.messageInput.style.overflowY = 'hidden';
  }
  renderAttachmentDrafts();
  renderClarificationBanner(state.pendingClarifications);
  updateInputState();
  loadMcpServers();
  loadProfile();
  await loadLlmProviders();

  // Only auto-load if we are on the main chat page
  if (elements.chatMessages) {
    const urlParams = new URLSearchParams(window.location.search);
    const cid = urlParams.get('conversation_id');
    if (cid) {
      await loadMessages(cid);
    }
  }

  // Update scheduled timers continuously
  window.setInterval(() => {
    document.querySelectorAll('.scheduled-countdown').forEach((el) => {
      const targetUnix = Number(el.dataset.target) || 0;
      if (!targetUnix) return;
      const diffStr = getScheduledDiffString(targetUnix);
      el.textContent = diffStr;
    });
  }, 1000);
}

function getScheduledDiffString(targetUnixStr) {
  const diff = Math.max(0, (targetUnixStr * 1000) - Date.now());
  if (diff <= 0) return 'Running soon...';

  const w = Math.floor(diff / (1000 * 60 * 60 * 24 * 7));
  const d = Math.floor((diff / (1000 * 60 * 60 * 24)) % 7);
  const h = Math.floor((diff / (1000 * 60 * 60)) % 24);
  const m = Math.floor((diff / 1000 / 60) % 60);
  const s = Math.floor((diff / 1000) % 60);

  const t = [];
  if (w > 0) t.push(`${w}w`);
  if (d > 0) t.push(`${d}d`);
  if (h > 0) t.push(`${h}h`);
  if (m > 0 || (h > 0 || d > 0 || w > 0)) t.push(`${m}m`);
  t.push(`${s}s`);

  return `Prompt executes in ${t.join(' ')}`;
}

document.addEventListener('DOMContentLoaded', initialize);
