<?php
/** @var array $user */
$userLabel = trim((string)($user['username'] ?? 'user'));
$assistantLabel = trim((string)get_setting('llm_model', 'assistant'));
if ($assistantLabel === '') {
  $assistantLabel = 'assistant';
}
$defaultProviderId = (int) get_setting('default_llm_provider_id', '0');
$routerEnabled = trim((string) get_setting('llm_router_enabled', '1')) !== '0' ? '1' : '0';
$routerTrigger = trim((string) get_setting('llm_router_trigger_name', 'router'));
if ($routerTrigger === '') {
  $routerTrigger = 'router';
}
?>
<section class="flex-1 flex flex-col h-[100dvh] min-h-0 overflow-hidden bg-transparent" data-user-label="<?= htmlspecialchars($userLabel) ?>" data-assistant-label="<?= htmlspecialchars($assistantLabel) ?>" data-default-provider-id="<?= $defaultProviderId ?>" data-router-enabled="<?= htmlspecialchars($routerEnabled) ?>" data-router-trigger="<?= htmlspecialchars($routerTrigger) ?>">
  <!-- Topbar matching Open WebUI aesthetic -->
  <header class="absolute inset-x-0 top-0 z-30 flex justify-between items-center px-4 md:px-6 py-3 bg-transparent pointer-events-none">
    <div class="flex items-center gap-2 md:gap-3">
      <button id="sidebarToggle" class="sidebar-toggle pointer-events-auto p-2 rounded-lg bg-[#151515]/30 border border-white/5 hover:bg-white/8 transition-colors text-zinc-300" type="button" title="Toggle sidebar">
        <i class="ph ph-list text-xl"></i>
      </button>
      <a href="/" class="pointer-events-auto flex items-center gap-2 p-2 rounded-lg bg-[#151515]/30 border border-white/5 hover:bg-white/8 transition-colors text-zinc-300" title="Go home">
        <i class="ph ph-house text-xl"></i>
      </a>
    </div>
    <div class="flex items-center gap-2 md:gap-3">
      <!-- Unified LLM selector pill (Mode + Provider + Model collapsed into one) -->
      <div class="pointer-events-auto hidden lg:flex items-center gap-1.5 px-3 py-1.5 rounded-2xl bg-[#151515]/40 border border-white/5 shadow-sm backdrop-blur-sm">
        <!-- Route mode selector (Auto / Manual) -->
        <i class="ph ph-robot text-sm text-zinc-500"></i>
        <select id="llmRouteSelect" class="bg-transparent text-zinc-200 text-xs focus:outline-none cursor-pointer" title="Routing mode"></select>
        <!-- Manual provider+model selects — shown only in manual mode -->
        <span id="llmTopbarManual" class="hidden items-center gap-1.5">
          <span class="text-zinc-600 text-xs select-none">·</span>
          <select id="llmProviderSelect" class="bg-transparent text-zinc-200 text-xs focus:outline-none cursor-pointer max-w-[140px]" title="Provider"></select>
          <span class="text-zinc-600 text-xs select-none">/</span>
          <select id="llmModelSelect" class="bg-transparent text-zinc-200 text-xs focus:outline-none cursor-pointer max-w-[160px]" title="Model"></select>
          <input id="llmModelInput" class="hidden bg-transparent text-zinc-200 text-xs focus:outline-none w-[160px]" type="text" placeholder="model name">
        </span>
        <!-- Auto badge — shown only in auto mode -->
        <span id="llmTopbarAuto" class="hidden items-center gap-1">
          <span class="text-zinc-600 text-xs select-none">·</span>
          <span class="text-xs text-amber-400 font-medium">Auto</span>
        </span>
      </div>
      <!-- Auto-approve toggle -->
      <label id="autoApproveToggleLabel" class="pointer-events-auto flex items-center gap-2 cursor-pointer select-none group" title="Auto-approve all tool calls without confirmation">
        <span class="text-[10px] font-bold uppercase tracking-widest text-zinc-500 group-hover:text-zinc-300 transition-colors hidden sm:block">Auto-approve</span>
        <div class="relative">
          <input type="checkbox" id="autoApproveToggle" class="sr-only peer">
          <div class="w-9 h-5 bg-zinc-700 rounded-full peer peer-checked:bg-amber-500 peer-checked:after:translate-x-full after:content-[''] after:absolute after:top-[2px] after:start-[2px] after:bg-white after:rounded-full after:h-4 after:w-4 after:transition-all transition-colors"></div>
        </div>
      </label>
      <div class="hidden sm:flex pointer-events-auto items-center gap-2 px-3 py-1.5 bg-[#151515]/40 border border-white/5 rounded-full text-xs text-zinc-400">
        <div class="w-1.5 h-1.5 rounded-full bg-green-500"></div>
        <?= htmlspecialchars($user['username']) ?>
      </div>
      <a href="/logout.php" class="pointer-events-auto p-2 rounded-lg bg-[#151515]/30 border border-white/5 text-zinc-400 hover:text-white hover:bg-[#2f2f2f] transition-colors" title="Logout">
        <i class="ph ph-sign-out text-xl"></i>
      </a>
    </div>
  </header>

  <main class="flex-1 flex min-h-0">
    <section id="chatPanel" class="flex-1 flex flex-col min-h-0 relative">
      
      <!-- Messages Container -->
      <div id="chatMessages" class="chat-messages flex-1 overflow-y-auto w-full max-w-7xl mx-auto px-5 md:px-6 pt-20 md:pt-24 pb-36 md:pb-40 custom-scrollbar">
        <div class="empty-state m-auto max-w-md text-center py-20">
          <div class="w-16 h-16 rounded-2xl bg-[#2f2f2f] flex items-center justify-center mx-auto mb-6 shadow-sm">
            <i class="ph ph-chats-circle text-3xl text-zinc-400"></i>
          </div>
          <h3 class="text-xl font-semibold mb-2 text-zinc-200">How can I help you today?</h3>
          <p class="text-zinc-500 text-sm">Create a new conversation or choose one from the sidebar to start sending prompts to the worker queue.</p>
        </div>
      </div>

      <!-- Pending Tool Approvals Banner -->
      <div id="toolApprovalBanner" class="hidden w-full max-w-7xl mx-auto px-5 md:px-6 py-1.5"></div>

      <!-- Pending Clarification Banner -->
      <div id="clarificationBanner" class="hidden w-full max-w-7xl mx-auto px-5 md:px-6 py-1.5"></div>

      <!-- Composer / Input Area -->
      <div class="composer absolute inset-x-0 bottom-0 z-20 w-full pointer-events-none">
        <div class="max-w-7xl mx-auto px-5 md:px-6 pb-4 pt-3 pointer-events-auto">
          <div class="composer-status-group inline-flex items-center gap-2 mb-2 px-2 py-1 rounded-2xl">
             <span id="streamStatus" class="stream-status text-[10px] uppercase tracking-wider font-bold text-zinc-500 bg-[#1a1a1a] px-2.5 py-1 rounded-lg border border-[#2a2a2a] shadow-sm">Idle</span>
             <span id="queueStatus" class="queue-status text-[10px] uppercase tracking-wider font-bold text-zinc-500"></span>
          </div>

          <div id="attachmentList" class="composer-attachments hidden"></div>

          <div class="relative transition-all focus-within:ring-1 focus-within:ring-blue-500/15">
            <label class="sr-only" for="messageInput">Message</label>
            <textarea id="messageInput" class="w-full bg-[#151515]/88 text-zinc-100 placeholder-zinc-600 border border-white/8 resize-none py-3 pl-20 pr-20 rounded-2xl focus:outline-none focus:ring-0 text-base max-h-[160px] custom-scrollbar shadow-sm backdrop-blur-sm" placeholder="Send a message..." rows="1"></textarea>

            <button id="attachmentButton" class="w-9 h-9 absolute left-3 top-1/2 -translate-y-1/2 flex items-center justify-center rounded-lg bg-transparent hover:bg-[#2f2f2f] text-zinc-300 hover:text-white transition-all disabled:opacity-30 disabled:text-zinc-600 active:scale-95" type="button" title="Attach files">
              <i class="ph ph-plus text-lg"></i>
            </button>
            
            <div class="absolute right-3 top-1/2 -translate-y-1/2 flex items-center">
              <button id="sendButton" class="w-9 h-9 flex items-center justify-center rounded-lg bg-transparent hover:bg-[#2f2f2f] text-zinc-300 hover:text-white transition-all disabled:opacity-30 disabled:text-zinc-600 active:scale-95" type="button" title="Send message">
                <i class="ph-fill ph-paper-plane-right text-lg"></i>
              </button>
            </div>
          </div>

          <input id="fileInput" class="hidden" type="file" multiple>
        </div>
        
        <div class="text-center mt-2 mb-1">
          <p class="text-[11px] text-zinc-500">
            Messages are processed based on operational need and may be sent to third-party LLM providers.
          </p>
        </div>
      </div>

    </section>
  </main>
</section>
