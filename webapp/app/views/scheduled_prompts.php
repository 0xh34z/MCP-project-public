<?php
/** @var array $scheduled_jobs */
/** @var array $conversations */
?>
<main class="w-full max-w-7xl mx-auto px-4 py-8 md:py-12 flex flex-col gap-8">
    
    <header class="flex flex-col md:flex-row md:items-end justify-between gap-4">
        <div class="flex flex-col gap-2">
            <div class="flex items-center gap-2 text-zinc-500 mb-2">
                <a href="/" class="flex items-center gap-1 hover:text-zinc-300 transition-colors">
                    <i class="ph ph-arrow-left"></i>
                    <span class="text-sm">Back to Console</span>
                </a>
                <span class="text-zinc-700">/</span>
                <span class="text-sm">Automations</span>
            </div>
            <h1 class="text-3xl font-bold text-white tracking-tight">Scheduled Queue</h1>
            <p class="text-zinc-400 text-sm">Review and manage prompts enqueued for future execution by background workers.</p>
        </div>
        <a href="/scheduled_new.php" class="flex items-center gap-2 px-4 py-2.5 bg-white hover:bg-zinc-200 text-zinc-900 rounded-xl font-bold transition-all shadow-lg active:scale-[0.98]">
            <i class="ph ph-plus-circle text-lg"></i>
            <span>New Schedule</span>
        </a>
    </header>

    <div class="flex flex-col gap-4">
        <?php if (isset($_GET['success'])): ?>
          <div class="bg-green-500/10 border border-green-500/20 text-green-400 px-4 py-3 rounded-xl text-sm flex items-center gap-3">
            <i class="ph ph-check-circle text-lg"></i>
            <span><strong>Success!</strong> Your prompt has been scheduled and added to the queue.</span>
          </div>
        <?php endif; ?>

        <?php if (isset($_GET['deleted'])): ?>
          <div class="bg-orange-500/10 border border-orange-500/20 text-orange-400 px-4 py-3 rounded-xl text-sm flex items-center gap-3">
            <i class="ph ph-trash text-lg"></i>
            <span>Scheduled job cancelled and removed from queue.</span>
          </div>
        <?php endif; ?>
    </div>

    <!-- Main Content Area -->
    <section class="bg-[#151515] border border-[#2a2a2a] rounded-2xl overflow-hidden shadow-xl">
        <div class="p-6 border-b border-[#2a2a2a] flex justify-between items-center bg-[#1a1a1a]">
            <div class="flex items-center gap-3">
                <div class="w-10 h-10 rounded-xl bg-blue-500/10 flex items-center justify-center text-blue-500">
                    <i class="ph ph-clock-countdown text-xl"></i>
                </div>
                <h2 class="text-lg font-semibold text-white">Active Queue</h2>
            </div>
            <span class="px-3 py-1 bg-[#2a2a2a] border border-[#3a3a3a] text-zinc-400 text-[10px] font-bold rounded-full uppercase tracking-widest">
                <?= count($scheduled_jobs) ?> Pending Tasks
            </span>
        </div>

        <div class="overflow-x-auto">
            <?php if (empty($scheduled_jobs)): ?>
                <div class="p-20 text-center flex flex-col items-center">
                    <div class="w-16 h-16 bg-[#1c1c1c] rounded-2xl flex items-center justify-center mb-6 text-zinc-700">
                        <i class="ph ph-calendar-blank text-3xl"></i>
                    </div>
                    <h3 class="text-xl font-semibold text-zinc-300 mb-2">The queue is empty</h3>
                    <p class="text-zinc-500 text-sm max-w-sm mb-8 italic">No upcoming tasks found. Automate your workflow by scheduling worker prompts.</p>
                    <a href="/scheduled_new.php" class="px-6 py-2.5 bg-zinc-800 hover:bg-zinc-700 text-zinc-200 rounded-xl text-sm font-semibold transition-colors border border-zinc-700">Schedule something now</a>
                </div>
            <?php else: ?>
                <table class="w-full text-sm text-left">
                    <thead class="bg-[#1c1c1c] text-zinc-500 uppercase text-[10px] tracking-widest font-bold">
                        <tr>
                            <th class="px-6 py-4">Scheduled For</th>
                            <th class="px-6 py-4">Prompt Preview</th>
                            <th class="px-6 py-4">Context</th>
                            <th class="px-6 py-4">Config</th>
                            <th class="px-6 py-4 text-right">Actions</th>
                        </tr>
                    </thead>
                    <tbody class="divide-y divide-[#2a2a2a]">
                        <?php foreach ($scheduled_jobs as $job): ?>
                            <tr class="group hover:bg-[#1c1c1c]/50 transition-colors">
                                <td class="px-6 py-5">
                                    <div class="flex items-center gap-3">
                                        <div class="w-2 h-2 rounded-full bg-blue-500 animate-pulse"></div>
                                        <div class="flex flex-col">
                                            <span class="text-zinc-200 font-medium"><?= date('M j, Y', strtotime($job['scheduled_at'])) ?></span>
                                            <span class="text-[10px] text-zinc-600 font-bold uppercase tracking-tighter"><?= date('H:i', strtotime($job['scheduled_at'])) ?> (Local)</span>
                                        </div>
                                    </div>
                                </td>
                                <td class="px-6 py-5 max-w-md">
                                    <div class="text-zinc-400 font-mono text-xs line-clamp-2 bg-black/10 p-2 rounded border border-[#2a2a2a]/30" title="<?= htmlspecialchars($job['prompt']) ?>">
                                        <?= htmlspecialchars($job['prompt']) ?>
                                    </div>
                                </td>
                                <td class="px-6 py-5 text-zinc-400">
                                    <a href="/?conversation_id=<?= $job['conversation_id'] ?>" class="flex items-center gap-1.5 hover:text-blue-400 transition-colors">
                                        <i class="ph ph-chat-centered-text text-zinc-600"></i>
                                        <span class="truncate max-w-[150px]"><?= htmlspecialchars($job['conversation_title'] ?? 'Global Context') ?></span>
                                    </a>
                                </td>
                                <td class="px-6 py-5">
                                    <div class="flex flex-wrap gap-1.5">
                                        <?php if ($job['repeat_count'] > 1): ?>
                                            <span class="px-1.5 py-0.5 bg-purple-500/10 text-purple-400 text-[9px] font-bold uppercase border border-purple-500/20 rounded-md ring-0" title="Repeats <?= $job['repeat_count'] ?> times">
                                                LOOP: <?= $job['repeat_count'] ?>x
                                            </span>
                                        <?php endif; ?>
                                        <?php if ($job['llm_model']): ?>
                                            <span class="px-1.5 py-0.5 bg-orange-500/10 text-orange-400 text-[9px] font-bold uppercase border border-orange-500/20 rounded-md tracking-tighter">
                                                DEV: <?= htmlspecialchars($job['llm_model']) ?>
                                            </span>
                                        <?php endif; ?>
                                        <?php if (!empty($job['auto_approve_tools'])): ?>
                                            <span class="px-1.5 py-0.5 bg-emerald-500/10 text-emerald-400 text-[9px] font-bold uppercase border border-emerald-500/20 rounded-md tracking-tighter">
                                                AUTO-APPROVE
                                            </span>
                                        <?php endif; ?>
                                    </div>
                                </td>
                                <td class="px-6 py-5 text-right">
                                    <form action="/scheduled.php" method="POST" onsubmit="return confirm('Cancel this scheduled job?');" class="inline">
                                        <input type="hidden" name="action" value="delete_job">
                                        <input type="hidden" name="job_id" value="<?= $job['id'] ?>">
                                        <button type="submit" class="p-2 text-zinc-600 hover:text-red-400 transition-all hover:bg-red-400/10 rounded-lg group" aria-label="Delete scheduled job">
                                            <i class="ph ph-trash text-lg"></i>
                                        </button>
                                    </form>
                                </td>
                            </tr>
                        <?php endforeach; ?>
                    </tbody>
                </table>
            <?php endif; ?>
        </div>
    </section>

</main>
