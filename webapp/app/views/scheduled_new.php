<main class="w-full max-w-7xl mx-auto px-4 py-8 md:py-12 flex flex-col gap-8">
    
    <header class="flex flex-col gap-2">
        <div class="flex items-center gap-2 text-zinc-500 mb-2">
            <a href="/scheduled.php" class="flex items-center gap-1 hover:text-zinc-300 transition-colors">
                <i class="ph ph-arrow-left"></i>
                <span class="text-sm">Back to Queue</span>
            </a>
            <span class="text-zinc-700">/</span>
            <span class="text-sm">New Task</span>
        </div>
        <h1 class="text-3xl font-bold text-white tracking-tight">Create Scheduled Task</h1>
        <p class="text-zinc-400 text-sm">Define a prompt to be automatically processed by the worker cluster at a specific time.</p>
    </header>

    <div class="bg-[#151515] border border-[#2a2a2a] rounded-3xl overflow-hidden shadow-2xl shadow-black/50">
        
        <form action="/scheduled_new.php" method="POST" class="flex flex-col">
          <input type="hidden" name="action" value="schedule_prompt">
          
          <div class="p-8 flex flex-col gap-8">
              <!-- Primary Input Section -->
              <div class="flex flex-col gap-4">
                  <label class="text-sm font-bold text-zinc-300 uppercase tracking-widest flex items-center gap-2">
                      <i class="ph ph-terminal-window text-lg text-blue-500"></i>
                      Assistant Instruction
                  </label>
                  <div class="relative group">
                      <textarea 
                        name="prompt" 
                        id="scheduledPromptInput"
                        placeholder="Type your prompt here... (e.g. Scan the subnet 192.168.2.0/24)" 
                        required
                        class="w-full bg-[#0a0a0a] border border-[#3a3a3a] rounded-2xl p-6 text-zinc-100 placeholder-zinc-700 focus:outline-none focus:ring-1 focus:ring-blue-500 transition-all min-h-[180px] shadow-inner text-base leading-relaxed"
                      ></textarea>
                      <div class="absolute bottom-4 right-4 flex gap-2">
                          <button type="button" onclick="usePortscanTemplate()" class="px-3 py-1.5 bg-[#1c1c1c] hover:bg-zinc-800 text-zinc-400 hover:text-zinc-200 text-[10px] font-bold uppercase tracking-widest rounded-lg border border-zinc-800 transition-all">
                              Load Portscan Template
                          </button>
                      </div>
                  </div>
              </div>

              <!-- Core Config Row -->
              <div class="grid grid-cols-1 md:grid-cols-2 gap-8 pt-4 border-t border-[#2a2a2a]/50">
                  <div class="flex flex-col gap-2">
                      <label class="text-xs font-bold text-zinc-500 uppercase tracking-widest ml-1">Execution Time</label>
                      <input 
                        type="datetime-local" 
                        name="scheduled_at" 
                        required 
                        value="<?= date('Y-m-d\TH:i') ?>"
                        class="w-full bg-[#0a0a0a] border border-[#3a3a3a] rounded-xl px-4 py-3 text-zinc-200 focus:outline-none focus:ring-1 focus:ring-blue-500 transition-all shadow-inner text-sm"
                      >
                  </div>
                  <div class="flex flex-col gap-2">
                      <label class="text-xs font-bold text-zinc-500 uppercase tracking-widest ml-1">Target Conversation</label>
                      <div class="relative">
                          <select name="conversation_id" class="w-full bg-[#0a0a0a] border border-[#3a3a3a] rounded-xl px-4 py-3 text-zinc-200 focus:outline-none focus:ring-1 focus:ring-blue-500 transition-all shadow-inner text-sm appearance-none cursor-pointer">
                            <option value="0">Create New Conversation</option>
                            <?php foreach ($conversations as $c): ?>
                              <option value="<?= $c['id'] ?>"><?= htmlspecialchars($c['title']) ?></option>
                            <?php endforeach; ?>
                          </select>
                          <div class="absolute right-4 top-3.5 pointer-events-none text-zinc-600">
                              <i class="ph ph-caret-down font-bold"></i>
                          </div>
                      </div>
                  </div>
              </div>

              <!-- Advanced Section -->
              <div class="bg-black/20 border border-white/5 rounded-2xl p-6 flex flex-col gap-6">
                <div class="flex items-center gap-2 text-zinc-400">
                    <i class="ph ph-sliders text-lg"></i>
                    <h3 class="text-xs font-bold uppercase tracking-widest">Advanced Configuration</h3>
                </div>
                
                <div class="grid grid-cols-1 md:grid-cols-2 gap-8">
                    <div class="grid grid-cols-2 gap-4">
                        <div class="flex flex-col gap-1.5">
                            <label class="text-[10px] font-bold text-zinc-600 uppercase ml-1">Repeat Count</label>
                            <input type="number" name="repeat_count" value="1" min="1" max="100" class="w-full bg-[#0a0a0a] border border-[#3a3a3a] rounded-lg px-3 py-2 text-zinc-300 text-xs">
                        </div>
                        <div class="flex flex-col gap-1.5">
                            <label class="text-[10px] font-bold text-zinc-600 uppercase ml-1">Interval (min)</label>
                            <input type="number" name="repeat_interval" value="0" min="0" class="w-full bg-[#0a0a0a] border border-[#3a3a3a] rounded-lg px-3 py-2 text-zinc-300 text-xs shadow-inner">
                        </div>
                    </div>

                    <div class="grid grid-cols-2 gap-4">
                        <div class="flex flex-col gap-1.5">
                            <label class="text-[10px] font-bold text-zinc-600 uppercase ml-1">Model Override</label>
                            <input type="text" name="llm_model" placeholder="llama3" value="<?= htmlspecialchars($settings['llm_model'] ?? '') ?>" class="w-full bg-[#0a0a0a] border border-[#3a3a3a] rounded-lg px-3 py-2 text-zinc-300 text-xs shadow-inner">
                        </div>
                        <div class="flex flex-col gap-1.5">
                            <label class="text-[10px] font-bold text-zinc-600 uppercase ml-1">API URL Override</label>
                            <input type="text" name="llm_api_url" placeholder="http://..." value="<?= htmlspecialchars($settings['llm_api_url'] ?? '') ?>" class="w-full bg-[#0a0a0a] border border-[#3a3a3a] rounded-lg px-3 py-2 text-zinc-300 text-xs shadow-inner">
                        </div>
                    </div>
                </div>

                <div class="flex flex-col gap-2">
                  <label class="text-[10px] font-bold text-zinc-600 uppercase ml-1 tracking-widest">Approvals</label>
                  <label class="group inline-flex items-center gap-3 bg-[#1d1d1d] border border-[#333] hover:border-blue-500/40 px-3 py-2 rounded-xl cursor-pointer transition-all w-fit">
                    <input type="checkbox" name="auto_approve_tools" value="1" class="w-3.5 h-3.5 accent-blue-500 rounded ring-0 outline-none cursor-pointer">
                    <span class="text-[11px] font-semibold text-zinc-300 group-hover:text-zinc-100 transition-colors">Auto-approve tool calls for this scheduled prompt</span>
                  </label>
                  <p class="text-[10px] text-zinc-500">When enabled, tool actions in this scheduled run skip manual approval prompts.</p>
                </div>

                <div class="flex flex-col gap-3">
                  <label class="text-[10px] font-bold text-zinc-600 uppercase ml-1Tracking-widest">Restrict MCP Servers / Available Tools</label>
                  <div class="flex flex-wrap gap-2">
                    <?php if (empty($mcp_servers)): ?>
                      <p class="text-[10px] text-zinc-600 italic">No tools available for override.</p>
                    <?php else: ?>
                      <?php foreach ($mcp_servers as $s): ?>
                        <label class="group flex items-center gap-2 bg-[#222] border border-[#333] hover:border-blue-500/50 px-3 py-1.5 rounded-xl cursor-pointer transition-all">
                          <input type="checkbox" name="mcp_servers[]" value="<?= htmlspecialchars($s['name']) ?>" checked class="w-3 h-3 accent-blue-500 rounded ring-0 outline-none cursor-pointer">
                          <span class="text-[10px] font-bold text-zinc-400 group-hover:text-zinc-200 transition-colors capitalize"><?= htmlspecialchars($s['name']) ?></span>
                        </label>
                      <?php endforeach; ?>
                    <?php endif; ?>
                  </div>
                </div>
              </div>
          </div>

          <div class="p-8 bg-[#1c1c1c] border-t border-[#2a2a2a] flex items-center justify-between">
              <p class="text-[10px] text-zinc-500 max-w-[200px] leading-relaxed uppercase font-bold tracking-tighter">Tasks will be processed sequentially based on server availability.</p>
              <button type="submit" class="px-8 py-3 bg-blue-600 hover:bg-blue-500 text-white font-bold rounded-2xl transition-all shadow-lg active:scale-[0.98]">
                Create Schedule
              </button>
          </div>
        </form>
    </div>
</main>

<script>
function usePortscanTemplate() {
  const input = document.getElementById('scheduledPromptInput');
  input.value = "/kali nmap -sV 192.168.2.0/24\n\nPlease scan the subnet for open ports and identify versions. Provide a summary of the most interesting findings.";
  input.focus();
}
</script>

