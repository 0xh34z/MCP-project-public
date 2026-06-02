<?php
/** @var array $conversations */

$stripTextTransportTags = static function (?string $value): string {
  $text = (string) ($value ?? '');
  $text = preg_replace('/\[(?:\/?\s*text\s*|\s*text\s*\/)\]/i', '', $text) ?? $text;
  return trim($text);
};
?>
<aside class="w-[260px] bg-[#171717] border-r border-[#2f2f2f] flex flex-col h-screen sticky top-0 overflow-hidden transition-all duration-300 z-[1000] text-[#ececec]" id="sidebar">
  
  <?php if ((current_user()['role'] ?? '') === 'admin'): ?>
    <div class="p-3 border-b border-[#2f2f2f] flex flex-col gap-2">
      <a href="/admin.php" class="w-full py-2 px-3 text-sm text-center bg-[#2f2f2f] hover:bg-[#3a3a3a] text-white rounded-lg transition-colors">Admin Dashboard &rarr;</a>
      <a href="/scheduled.php" class="flex items-center gap-2 py-2 px-3 text-sm rounded-lg hover:bg-[#2f2f2f] transition-colors <?= str_contains($_SERVER['PHP_SELF'], 'scheduled') ? 'bg-[#2f2f2f]' : '' ?>">
        <i class="ph ph-clock"></i>
        <span>Scheduled Prompts</span>
      </a>
    </div>
  <?php endif; ?>

  <div class="flex justify-between items-center px-4 py-3">
    <div>
      <p class="text-xs font-semibold text-zinc-500 uppercase tracking-wider mb-0.5">Workspace</p>
      <h2 class="text-base font-medium m-0">Conversations</h2>
    </div>
    <div class="flex items-center gap-2">
      <a href="/" class="w-8 h-8 flex items-center justify-center rounded-full bg-zinc-800 hover:bg-zinc-700 text-zinc-300 transition-colors" title="Home">
        <i class="ph ph-house text-lg"></i>
      </a>
      <button id="newConversationButton" type="button" class="w-8 h-8 flex items-center justify-center rounded-full bg-zinc-800 hover:bg-zinc-700 text-zinc-300 transition-colors" title="New Chat">
        <i class="ph ph-plus text-lg"></i>
      </button>
    </div>
  </div>

  <div id="conversationList" class="conversation-list flex-1 overflow-y-hidden hover:overflow-y-auto px-3 pb-3 custom-scrollbar">
    <?php foreach ($conversations as $conversation): ?>
      <?php $cleanTitle = $stripTextTransportTags($conversation['title'] ?? ''); ?>
      <?php $cleanSnippet = $stripTextTransportTags($conversation['snippet'] ?? ''); ?>
      <div class="conversation-row group relative flex items-center mb-1">
        <button class="conversation-item flex-1 text-left px-3 py-2.5 rounded-lg hover:bg-[#2f2f2f] transition-all overflow-hidden" type="button" data-id="<?= (int) $conversation['id'] ?>">
          <div class="conversation-item-content flex flex-col gap-0.5">
            <span class="conversation-title text-sm font-medium text-zinc-200 truncate"><?= htmlspecialchars($cleanTitle, ENT_QUOTES, 'UTF-8', false) ?></span>
            <?php if ($cleanSnippet !== ''): ?>
              <span class="conversation-snippet text-xs text-zinc-500 truncate"><?= htmlspecialchars(mb_strimwidth($cleanSnippet, 0, 80, '...'), ENT_QUOTES, 'UTF-8', false) ?></span>
            <?php endif; ?>
          </div>
        </button>
        <button class="conversation-delete absolute right-2 opacity-0 group-hover:opacity-100 p-1.5 rounded-md hover:bg-zinc-700 hover:text-red-400 text-zinc-400 transition-all" type="button" data-id="<?= (int) $conversation['id'] ?>" title="Delete conversation">
          <i class="ph ph-trash"></i>
        </button>
      </div>
    <?php endforeach; ?>
  </div>

  <div class="p-3 border-t border-[#2f2f2f] bg-[#1a1a1a]">
    <p class="text-xs font-semibold text-zinc-500 uppercase tracking-wider mb-3 px-1">Active Tools</p>
    <div id="mcpServerList" class="flex flex-col gap-2">
      <div class="text-xs text-zinc-500 italic px-1">Loading tools...</div>
    </div>
  </div>

  <details class="group border-t border-[#2f2f2f] bg-[#1a1a1a] [&_summary::-webkit-details-marker]:hidden">
    <summary class="flex items-center justify-between px-4 py-3 cursor-pointer hover:bg-[#2f2f2f] transition-colors select-none">
      <p class="text-xs font-semibold text-zinc-500 uppercase tracking-wider m-0">Identity & Context</p>
      <i class="ph ph-caret-down text-zinc-500 transition-transform group-open:rotate-180"></i>
    </summary>
    <div class="p-4 pt-1 flex flex-col gap-4">
      <div class="flex flex-col gap-1.5">
        <div class="flex justify-between items-center">
          <label class="text-xs font-medium text-zinc-400">AI Persona</label>
          <div class="flex gap-2">
            <a href="#" class="text-[10px] text-blue-400 hover:text-blue-300 transition-colors uppercase" id="uploadPersonaLink" title="Upload .md file">Upload</a>
            <a href="#" class="text-[10px] text-blue-400 hover:text-blue-300 transition-colors uppercase" id="viewPersonaLink">Edit</a>
          </div>
          <input type="file" id="personaFileInput" accept=".md" class="hidden">
        </div>
        <div id="personaPreview" class="text-xs text-zinc-500 italic truncate bg-zinc-800/50 px-2 py-1.5 rounded border border-zinc-700/50">No persona active</div>
      </div>
      
      <div class="flex flex-col gap-1.5">
        <div class="flex justify-between items-center">
          <label class="text-xs font-medium text-zinc-400">Infrastructure</label>
          <div class="flex gap-2">
            <a href="#" class="text-[10px] text-blue-400 hover:text-blue-300 transition-colors uppercase" id="uploadInfrastructureLink" title="Upload .md file">Upload</a>
            <a href="#" class="text-[10px] text-blue-400 hover:text-blue-300 transition-colors uppercase" id="viewInfrastructureLink">Edit</a>
          </div>
          <input type="file" id="infrastructureFileInput" accept=".md" class="hidden">
        </div>
        <div id="blueprintsPreview" class="text-xs text-zinc-500 italic truncate bg-zinc-800/50 px-2 py-1.5 rounded border border-zinc-700/50">No infrastructure active</div>
      </div>
      <button id="saveIdentityButton" class="w-full py-2 bg-zinc-800 hover:bg-zinc-700 text-sm text-zinc-200 rounded-lg transition-colors border border-zinc-700">Save Identity</button>
    </div>
  </details>

  <!-- Hidden textareas -->
  <textarea id="persona" class="hidden"></textarea>
  <textarea id="blueprints" class="hidden"></textarea>

  <!-- Modal -->
  <dialog id="identityDialog" class="bg-[#212121] text-zinc-200 border border-[#2f2f2f] rounded-xl p-6 w-[90%] max-w-2xl shadow-2xl backdrop:bg-black/80 backdrop:backdrop-blur-sm">
    <div class="flex flex-col gap-4">
      <h3 id="dialogTitle" class="text-lg font-medium m-0">Edit Content</h3>
      <textarea id="dialogTextarea" rows="15" class="w-full bg-[#171717] border border-[#2f2f2f] rounded-lg p-3 text-sm font-mono focus:outline-none focus:border-blue-500/50 resize-y"></textarea>
      <div class="flex justify-end gap-3 mt-2">
        <button id="closeDialogButton" class="px-4 py-2 rounded-lg text-sm bg-zinc-800 hover:bg-zinc-700 transition-colors">Cancel</button>
        <button id="saveDialogButton" class="px-4 py-2 rounded-lg text-sm bg-blue-600 hover:bg-blue-500 text-white transition-colors">Apply Changes</button>
      </div>
    </div>
  </dialog>
</aside>

