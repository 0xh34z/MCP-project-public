<?php
/** @var array $pending_users */
/** @var array $settings */
/** @var string|null $settings_saved */
?>
<main class="w-full max-w-7xl mx-auto px-4 py-8 md:py-12 flex flex-col gap-8">
    
    <header class="flex flex-col gap-2">
        <div class="flex items-center gap-2 text-zinc-500 mb-2">
            <a href="/" class="flex items-center gap-1 hover:text-zinc-300 transition-colors">
                <i class="ph ph-arrow-left"></i>
                <span class="text-sm">Back to App</span>
            </a>
            <span class="text-zinc-700">/</span>
            <span class="text-sm">Admin</span>
        </div>
        <h1 class="text-3xl font-bold text-white tracking-tight">Admin Dashboard</h1>
        <p class="text-zinc-400">Welcome, <strong><?= htmlspecialchars(current_user()['username'] ?? '') ?></strong>. Manage access and local MCP configuration.</p>
    </header>

    <?php if ($settings_saved === 'ok'): ?>
      <div class="bg-green-500/10 border border-green-500/20 text-green-400 px-4 py-3 rounded-xl text-sm flex items-center gap-2">
        <i class="ph ph-check-circle text-lg"></i>
        Settings saved successfully.
      </div>
    <?php elseif ($settings_saved === 'error'): ?>
      <div class="bg-red-500/10 border border-red-500/20 text-red-400 px-4 py-3 rounded-xl text-sm flex items-center gap-2">
        <i class="ph ph-warning-circle text-lg"></i>
        Failed to save settings.
      </div>
    <?php endif; ?>

    <div class="grid grid-cols-1 lg:grid-cols-3 gap-8">
        
        <!-- Main Configuration Section -->
        <div class="lg:col-span-2 flex flex-col gap-8">
            
            <!-- User Access Section -->
            <section class="bg-[#151515] border border-[#2a2a2a] rounded-2xl overflow-hidden">
                <div class="p-6 border-b border-[#2a2a2a] flex justify-between items-center">
                    <div class="flex items-center gap-3">
                        <div class="w-10 h-10 rounded-xl bg-blue-500/10 flex items-center justify-center text-blue-500">
                            <i class="ph ph-users text-xl"></i>
                        </div>
                        <h2 class="text-lg font-semibold text-white">Pending Requests</h2>
                    </div>
                    <?php if (!empty($pending_users)): ?>
                        <span class="px-2 py-0.5 bg-blue-500 text-white text-[10px] font-bold rounded-full uppercase tracking-wider"><?= count($pending_users) ?> New</span>
                    <?php endif; ?>
                </div>

                <div class="p-0 overflow-x-auto">
                    <?php if (empty($pending_users)): ?>
                        <div class="p-12 text-center">
                            <div class="w-12 h-12 bg-[#1c1c1c] rounded-full flex items-center justify-center mx-auto mb-4 text-zinc-600">
                                <i class="ph ph-user-circle-plus text-2xl"></i>
                            </div>
                            <p class="text-zinc-500 text-sm italic">No pending registrations at the moment.</p>
                        </div>
                    <?php else: ?>
                        <table class="w-full text-sm text-left">
                            <thead class="bg-[#1c1c1c] text-zinc-500 uppercase text-[10px] tracking-widest font-bold">
                                <tr>
                                    <th class="px-6 py-4">Username</th>
                                    <th class="px-6 py-4">Requested On</th>
                                    <th class="px-6 py-4 text-right">Actions</th>
                                </tr>
                            </thead>
                            <tbody class="divide-y divide-[#2a2a2a]">
                                <?php foreach ($pending_users as $u): ?>
                                    <tr class="hover:bg-[#1c1c1c]/50 transition-colors">
                                        <td class="px-6 py-4 font-medium text-zinc-200"><?= htmlspecialchars($u['username']) ?></td>
                                        <td class="px-6 py-4 text-zinc-500"><?= date('M j, Y H:i', strtotime($u['created_at'])) ?></td>
                                        <td class="px-6 py-4 text-right flex justify-end gap-2">
                                            <form method="post" class="inline">
                                                <input type="hidden" name="user_id" value="<?= $u['id'] ?>">
                                                <input type="hidden" name="action" value="approve">
                                                <button type="submit" class="px-3 py-1.5 bg-zinc-200 hover:bg-white text-zinc-900 rounded-lg text-xs font-semibold transition-colors">Approve</button>
                                            </form>
                                            <form method="post" class="inline">
                                                <input type="hidden" name="user_id" value="<?= $u['id'] ?>">
                                                <input type="hidden" name="action" value="reject">
                                                <button type="submit" class="px-3 py-1.5 bg-red-500/10 hover:bg-red-500/20 text-red-500 rounded-lg text-xs font-semibold transition-colors border border-red-500/20">Reject</button>
                                            </form>
                                        </td>
                                    </tr>
                                <?php endforeach; ?>
                            </tbody>
                        </table>
                    <?php endif; ?>
                </div>
            </section>

            <!-- Unified LLM Control -->
            <section class="bg-[#151515] border border-[#2a2a2a] rounded-2xl overflow-hidden">
                <div class="p-6 border-b border-[#2a2a2a]">
                    <div class="flex items-center gap-3">
                        <div class="w-10 h-10 rounded-xl bg-purple-500/10 flex items-center justify-center text-purple-500">
                            <i class="ph ph-brain text-xl"></i>
                        </div>
                        <div>
                            <h2 class="text-lg font-semibold text-white">LLM Control</h2>
                            <p class="text-xs text-zinc-500 mt-0.5">One place for defaults, router rules, and provider management.</p>
                        </div>
                    </div>
                </div>

                <form method="post" class="p-8 flex flex-col gap-6">
                    <input type="hidden" name="action" value="save_settings">

                    <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
                        <div class="flex flex-col gap-1.5">
                            <label class="text-xs font-medium text-zinc-400">Default Provider</label>
                            <select name="default_llm_provider_id" class="w-full bg-[#0a0a0a] border border-[#3a3a3a] rounded-xl px-4 py-2.5 text-zinc-200 focus:outline-none focus:ring-1 focus:ring-blue-500 shadow-inner text-sm">
                                <option value="0" <?= (int)($settings['default_llm_provider_id'] ?? 0) === 0 ? 'selected' : '' ?>>Legacy settings only</option>
                                <?php foreach ($llm_providers as $provider): ?>
                                    <option value="<?= (int) $provider['id'] ?>" <?= (int)($settings['default_llm_provider_id'] ?? 0) === (int) $provider['id'] ? 'selected' : '' ?>>
                                        <?= htmlspecialchars($provider['name']) ?> (<?= htmlspecialchars($provider['provider_type']) ?>)
                                    </option>
                                <?php endforeach; ?>
                            </select>
                        </div>

                        <div class="flex flex-col gap-1.5">
                            <label class="text-xs font-medium text-zinc-400">Auto Router Enabled</label>
                            <?php $routerEnabled = (string)($settings['llm_router_enabled'] ?? '1'); ?>
                            <select name="llm_router_enabled" class="w-full bg-[#0a0a0a] border border-[#3a3a3a] rounded-xl px-4 py-2.5 text-zinc-200 focus:outline-none focus:ring-1 focus:ring-blue-500 shadow-inner text-sm">
                                <option value="1" <?= $routerEnabled === '1' ? 'selected' : '' ?>>Yes</option>
                                <option value="0" <?= $routerEnabled === '0' ? 'selected' : '' ?>>No</option>
                            </select>
                        </div>
                    </div>

                    <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
                        <div class="flex flex-col gap-1.5 md:col-span-2">
                            <label class="text-xs font-medium text-zinc-400">Router Trigger Name</label>
                            <input type="text" name="llm_router_trigger_name" value="<?= htmlspecialchars($settings['llm_router_trigger_name'] ?? 'router') ?>" class="w-full bg-[#0a0a0a] border border-[#3a3a3a] rounded-xl px-4 py-2.5 text-zinc-200 focus:outline-none focus:ring-1 focus:ring-blue-500 shadow-inner text-sm" placeholder="router">
                            <span class="text-[10px] text-zinc-600 ml-1">Routing activates when selected provider name contains this text, or model is auto/router.</span>
                        </div>
                    </div>

                    <div class="grid grid-cols-1 md:grid-cols-2 gap-4 pt-2">
                        <div class="bg-[#0f0f0f] border border-[#2a2a2a] rounded-xl p-4 flex flex-col gap-3">
                            <div class="text-[10px] uppercase tracking-widest text-zinc-500">Low Tier</div>
                            <input type="text" name="llm_router_low_model" value="<?= htmlspecialchars($settings['llm_router_low_model'] ?? 'deepseek/deepseek-v4-flash') ?>" class="w-full bg-[#0a0a0a] border border-[#3a3a3a] rounded-xl px-3 py-2 text-zinc-200 text-sm" placeholder="model name">
                            <select name="llm_router_low_provider_id" class="w-full bg-[#0a0a0a] border border-[#3a3a3a] rounded-xl px-3 py-2 text-zinc-200 text-sm">
                                <option value="0" <?= (int)($settings['llm_router_low_provider_id'] ?? 0) === 0 ? 'selected' : '' ?>>Auto provider by model</option>
                                <?php foreach ($llm_providers as $provider): ?>
                                    <option value="<?= (int) $provider['id'] ?>" <?= (int)($settings['llm_router_low_provider_id'] ?? 0) === (int) $provider['id'] ? 'selected' : '' ?>>
                                        <?= htmlspecialchars($provider['name']) ?>
                                    </option>
                                <?php endforeach; ?>
                            </select>
                        </div>

                        <div class="bg-[#0f0f0f] border border-[#2a2a2a] rounded-xl p-4 flex flex-col gap-3">
                            <div class="text-[10px] uppercase tracking-widest text-zinc-500">High Tier</div>
                            <input type="text" name="llm_router_high_model" value="<?= htmlspecialchars($settings['llm_router_high_model'] ?? 'deepseek/deepseek-v4-flash') ?>" class="w-full bg-[#0a0a0a] border border-[#3a3a3a] rounded-xl px-3 py-2 text-zinc-200 text-sm" placeholder="model name">
                            <select name="llm_router_high_provider_id" class="w-full bg-[#0a0a0a] border border-[#3a3a3a] rounded-xl px-3 py-2 text-zinc-200 text-sm">
                                <option value="0" <?= (int)($settings['llm_router_high_provider_id'] ?? 0) === 0 ? 'selected' : '' ?>>Auto provider by model</option>
                                <?php foreach ($llm_providers as $provider): ?>
                                    <option value="<?= (int) $provider['id'] ?>" <?= (int)($settings['llm_router_high_provider_id'] ?? 0) === (int) $provider['id'] ? 'selected' : '' ?>>
                                        <?= htmlspecialchars($provider['name']) ?>
                                    </option>
                                <?php endforeach; ?>
                            </select>
                        </div>
                    </div>

                    <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
                        <div class="flex flex-col gap-1.5 col-span-1">
                            <label class="text-xs font-medium text-zinc-400">Timeout (s)</label>
                            <input type="number" name="llm_timeout" value="<?= (int)($settings['llm_timeout'] ?? 90) ?>" min="5" max="600" class="w-full bg-[#0a0a0a] border border-[#3a3a3a] rounded-xl px-4 py-2.5 text-zinc-200 focus:outline-none focus:ring-1 focus:ring-blue-500 shadow-inner text-sm">
                        </div>
                        <div class="flex flex-col gap-1.5 col-span-1">
                            <label class="text-xs font-medium text-zinc-400">Poll (s)</label>
                            <input type="number" name="poll_interval" value="<?= (float)($settings['poll_interval'] ?? 2) ?>" min="0.5" max="60" step="0.5" class="w-full bg-[#0a0a0a] border border-[#3a3a3a] rounded-xl px-4 py-2.5 text-zinc-200 focus:outline-none focus:ring-1 focus:ring-blue-500 shadow-inner text-sm">
                        </div>
                        <div class="flex flex-col gap-1.5 col-span-2">
                            <label class="text-xs font-medium text-zinc-400">Max Tool Calls Per Run</label>
                            <input type="number" name="max_tool_calls" value="<?= (int)($settings['max_tool_calls'] ?? -1) ?>" min="-1" max="200" class="w-full bg-[#0a0a0a] border border-[#3a3a3a] rounded-xl px-4 py-2.5 text-zinc-200 focus:outline-none focus:ring-1 focus:ring-blue-500 shadow-inner text-sm">
                            <span class="text-[10px] text-zinc-600 ml-1">Default is <strong>-1</strong> for unlimited chaining.</span>
                        </div>
                    </div>

                    <div class="grid grid-cols-1 md:grid-cols-2 gap-6 pt-4 border-t border-[#2a2a2a]/50">
                        <div class="flex flex-col gap-1.5">
                            <label class="text-xs font-medium text-zinc-400">Tool Permission Required</label>
                            <?php $toolPermRequiredInline = (string)($settings['tool_permission_required'] ?? '1'); ?>
                            <select name="tool_permission_required" class="bg-[#0a0a0a] border border-[#3a3a3a] rounded-xl px-4 py-2.5 text-zinc-200 text-sm">
                                <option value="1" <?= $toolPermRequiredInline === '1' ? 'selected' : '' ?>>Yes (ask before tool calls)</option>
                                <option value="0" <?= $toolPermRequiredInline === '0' ? 'selected' : '' ?>>No (auto run tools)</option>
                            </select>
                        </div>
                        <div class="flex flex-col gap-1.5">
                            <label class="text-xs font-medium text-zinc-400">Context Summary</label>
                            <?php $summaryEnabled = (string)($settings['context_summary_enabled'] ?? '1'); ?>
                            <select name="context_summary_enabled" class="bg-[#0a0a0a] border border-[#3a3a3a] rounded-xl px-4 py-2.5 text-zinc-200 text-sm">
                                <option value="1" <?= $summaryEnabled === '1' ? 'selected' : '' ?>>Enabled</option>
                                <option value="0" <?= $summaryEnabled === '0' ? 'selected' : '' ?>>Disabled</option>
                            </select>
                        </div>
                    </div>

                <button type="submit" class="w-full mt-2 py-3 bg-white text-zinc-900 hover:bg-zinc-200 font-bold rounded-xl transition-all shadow-lg active:scale-[0.98]">Save All Settings</button>
                </form>

                <div class="p-6 border-t border-[#2a2a2a] flex items-center justify-between gap-4">
                    <div class="flex items-center gap-3">
                        <div class="w-10 h-10 rounded-xl bg-cyan-500/10 flex items-center justify-center text-cyan-400">
                            <i class="ph ph-plug text-xl"></i>
                        </div>
                        <div>
                            <h2 class="text-lg font-semibold text-white">Providers</h2>
                            <p class="text-xs text-zinc-500 mt-0.5">Add and maintain model providers.</p>
                        </div>
                    </div>
                </div>

                <div class="p-8 flex flex-col gap-8">
                    <form method="post" class="grid grid-cols-1 md:grid-cols-2 gap-4 bg-[#0f0f0f] border border-[#2a2a2a] rounded-2xl p-5">
                        <input type="hidden" name="action" value="add_llm_provider">
                        <div class="flex flex-col gap-1.5">
                            <label class="text-xs font-medium text-zinc-400">Name</label>
                            <input type="text" name="name" class="w-full bg-[#0a0a0a] border border-[#3a3a3a] rounded-xl px-4 py-2.5 text-zinc-200 text-sm" placeholder="OpenAI API">
                        </div>
                        <div class="flex flex-col gap-1.5">
                            <label class="text-xs font-medium text-zinc-400">Type</label>
                            <select name="provider_type" class="w-full bg-[#0a0a0a] border border-[#3a3a3a] rounded-xl px-4 py-2.5 text-zinc-200 text-sm">
                                <option value="openai-compatible">OpenAI Compatible</option>
                            </select>
                        </div>
                        <div class="flex flex-col gap-1.5 md:col-span-2">
                            <label class="text-xs font-medium text-zinc-400">Base URL</label>
                            <input type="text" name="base_url" class="w-full bg-[#0a0a0a] border border-[#3a3a3a] rounded-xl px-4 py-2.5 text-zinc-200 text-sm" placeholder="https://api.example.com/v1">
                        </div>
                        <div class="flex flex-col gap-1.5">
                            <label class="text-xs font-medium text-zinc-400">Default Model</label>
                            <input type="text" name="default_model" class="w-full bg-[#0a0a0a] border border-[#3a3a3a] rounded-xl px-4 py-2.5 text-zinc-200 text-sm" placeholder="gpt-4o-mini">
                        </div>
                        <div class="flex flex-col gap-1.5">
                            <label class="text-xs font-medium text-zinc-400">API Key</label>
                            <input type="password" name="api_key" class="w-full bg-[#0a0a0a] border border-[#3a3a3a] rounded-xl px-4 py-2.5 text-zinc-200 text-sm" placeholder="optional">
                        </div>
                        <label class="flex items-center gap-2 text-xs text-zinc-400 md:col-span-2">
                            <input type="checkbox" name="is_active" checked class="rounded border-zinc-600 bg-[#0a0a0a] text-blue-500 focus:ring-blue-500">
                            Enabled
                        </label>
                        <button type="submit" class="md:col-span-2 w-full py-3 bg-white text-zinc-900 hover:bg-zinc-200 font-bold rounded-xl transition-all shadow-lg active:scale-[0.98]">Add Provider</button>
                    </form>

                    <div class="grid grid-cols-1 xl:grid-cols-2 gap-4">
                        <?php if (empty($llm_providers)): ?>
                            <div class="text-sm text-zinc-500 italic">No providers configured yet.</div>
                        <?php endif; ?>
                        <?php foreach ($llm_providers as $provider): ?>
                            <?php $stats = $provider_stats[(int) $provider['id']] ?? ['job_count' => 0, 'last_used_at' => '']; ?>
                            <form method="post" class="bg-[#0f0f0f] border border-[#2a2a2a] rounded-2xl p-5 flex flex-col gap-4">
                                <input type="hidden" name="provider_id" value="<?= (int) $provider['id'] ?>">
                                <div class="flex items-start justify-between gap-3">
                                    <div class="flex-1 min-w-0">
                                        <input type="text" name="name" value="<?= htmlspecialchars($provider['name']) ?>" class="w-full bg-[#0a0a0a] border border-[#3a3a3a] rounded-xl px-4 py-2 text-zinc-200 text-sm font-medium">
                                        <div class="flex flex-wrap items-center gap-2 mt-3 text-[10px] uppercase tracking-widest text-zinc-500">
                                            <span class="px-2 py-1 rounded-full bg-[#1b1b1b] border border-[#2f2f2f]">ID <?= (int) $provider['id'] ?></span>
                                            <span class="px-2 py-1 rounded-full bg-[#1b1b1b] border border-[#2f2f2f]">Requests <?= (int) ($stats['job_count'] ?? 0) ?></span>
                                            <span class="px-2 py-1 rounded-full bg-[#1b1b1b] border border-[#2f2f2f]">
                                                <?php if (!empty($stats['last_used_at'])): ?>
                                                  Last used <?= htmlspecialchars(date('M j, H:i', strtotime($stats['last_used_at']))) ?>
                                                <?php else: ?>
                                                  Never used
                                                <?php endif; ?>
                                            </span>
                                            <?php if ((int)($settings['default_llm_provider_id'] ?? 0) === (int) $provider['id']): ?>
                                              <span class="px-2 py-1 rounded-full bg-blue-500/10 border border-blue-500/20 text-blue-300">Default</span>
                                            <?php endif; ?>
                                        </div>
                                    </div>
                                    <label class="flex items-center gap-2 text-xs text-zinc-400 whitespace-nowrap shrink-0">
                                        <input type="checkbox" name="is_active" <?= (int)($provider['is_active'] ?? 0) ? 'checked' : '' ?> class="rounded border-zinc-600 bg-[#0a0a0a] text-blue-500 focus:ring-blue-500">
                                        Active
                                    </label>
                                </div>
                                <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
                                    <select name="provider_type" class="bg-[#0a0a0a] border border-[#3a3a3a] rounded-xl px-4 py-2.5 text-zinc-200 text-sm">
                                        <option value="openai-compatible" <?= ($provider['provider_type'] ?? '') === 'openai-compatible' ? 'selected' : '' ?>>OpenAI Compatible</option>
                                    </select>
                                    <input type="text" name="default_model" value="<?= htmlspecialchars($provider['default_model'] ?? '') ?>" class="bg-[#0a0a0a] border border-[#3a3a3a] rounded-xl px-4 py-2.5 text-zinc-200 text-sm" placeholder="Default model">
                                </div>
                                <input type="text" name="base_url" value="<?= htmlspecialchars($provider['base_url'] ?? '') ?>" class="bg-[#0a0a0a] border border-[#3a3a3a] rounded-xl px-4 py-2.5 text-zinc-200 text-sm" placeholder="Base URL">
                                <input type="password" name="api_key" value="" class="bg-[#0a0a0a] border border-[#3a3a3a] rounded-xl px-4 py-2.5 text-zinc-200 text-sm" placeholder="Leave blank to keep existing key">
                                <div class="flex flex-wrap items-center justify-between gap-3 pt-1">
                                    <div class="text-[10px] uppercase tracking-widest text-zinc-600">
                                        <?= htmlspecialchars($provider['provider_type'] ?? 'openai-compatible') ?>
                                    </div>
                                    <div class="flex items-center gap-2">
                                        <button type="submit" name="action" value="save_llm_provider" class="px-3 py-1.5 bg-zinc-200 hover:bg-white text-zinc-900 rounded-lg text-xs font-semibold transition-colors">Save</button>
                                    </div>
                                </div>
                            </form>
                            <form method="post" class="-mt-2 flex justify-end">
                                <input type="hidden" name="action" value="delete_llm_provider">
                                <input type="hidden" name="provider_id" value="<?= (int) $provider['id'] ?>">
                                <button type="submit" class="px-3 py-1.5 bg-red-500/10 hover:bg-red-500/20 text-red-300 rounded-lg text-xs font-semibold transition-colors border border-red-500/20" onclick="return confirm('Delete this provider? This cannot be undone.')">Delete Provider</button>
                            </form>
                        <?php endforeach; ?>
                    </div>
                </div>
            </section>

        </div>

        <!-- Sidebar Components -->
        <div class="lg:col-span-1 flex flex-col gap-8">
            
            <!-- MCP Servers Section -->
            <section class="bg-[#151515] border border-[#2a2a2a] rounded-2xl overflow-hidden flex flex-col">
                <div class="p-6 border-b border-[#2a2a2a] flex items-center justify-between">
                    <div class="flex items-center gap-3">
                        <div class="w-8 h-8 rounded-lg bg-orange-500/10 flex items-center justify-center text-orange-500">
                            <i class="ph ph-plug-connected text-lg"></i>
                        </div>
                        <h2 class="text-base font-semibold text-white">MCP Servers</h2>
                    </div>
                </div>

                <div class="flex-1 flex flex-col">
                    <?php if (empty($mcp_servers)): ?>
                        <div class="p-8 text-center text-zinc-600 text-xs italic">No tools connected.</div>
                    <?php else: ?>
                        <div class="divide-y divide-[#2a2a2a]">
                            <?php foreach ($mcp_servers as $s): ?>
                                <div class="p-4 flex flex-col gap-3 group">
                                    <div class="flex justify-between items-start">
                                        <div class="flex flex-col">
                                            <span class="text-sm font-semibold text-zinc-200"><?= htmlspecialchars($s['name']) ?></span>
                                            <span class="text-[10px] text-zinc-500 uppercase tracking-tighter"><?= htmlspecialchars($s['type'] === 'sse' ? 'HTTP STREAM' : $s['type']) ?></span>
                                        </div>
                                        <form method="post">
                                            <input type="hidden" name="action" value="toggle_mcp_server">
                                            <input type="hidden" name="mcp_id" value="<?= $s['id'] ?>">
                                            <input type="hidden" name="active" value="<?= $s['is_active'] ? '0' : '1' ?>">
                                            <button type="submit" class="relative inline-flex h-5 w-9 items-center rounded-full transition-colors <?= $s['is_active'] ? 'bg-blue-600' : 'bg-zinc-700' ?>">
                                                <span class="inline-block h-3 w-3 transform rounded-full bg-white transition-transform <?= $s['is_active'] ? 'translate-x-5' : 'translate-x-1' ?>"></span>
                                            </button>
                                        </form>
                                    </div>
                                    <div class="text-[10px] font-mono text-zinc-600 break-all bg-black/20 p-2 rounded border border-[#2a2a2a]/50">
                                        <?= htmlspecialchars($s['type'] === 'stdio' ? $s['command'] : $s['url']) ?>
                                    </div>
                                    <form method="post" onsubmit="return confirm('Delete this server?');" class="hidden group-hover:block">
                                        <input type="hidden" name="action" value="delete_mcp_server">
                                        <input type="hidden" name="mcp_id" value="<?= $s['id'] ?>">
                                        <button type="submit" class="text-[10px] text-red-400 hover:text-red-300 transition-colors uppercase font-bold tracking-widest">Delete Server</button>
                                    </form>
                                </div>
                            <?php endforeach; ?>
                        </div>
                    <?php endif; ?>
                </div>

                <div class="p-6 bg-[#1a1a1a] border-t border-[#2a2a2a]">
                    <h3 class="text-xs font-bold text-zinc-400 uppercase tracking-widest mb-4">Add New Server</h3>
                    <form method="post" class="flex flex-col gap-3">
                        <input type="hidden" name="action" value="add_mcp_server">
                        <input type="text" name="mcp_name" placeholder="Name (e.g. Kali)" required class="w-full bg-[#0a0a0a] border border-[#3a3a3a] rounded-lg px-3 py-2 text-xs text-zinc-200">
                        <select name="mcp_type" class="w-full bg-[#0a0a0a] border border-[#3a3a3a] rounded-lg px-3 py-2 text-xs text-zinc-200">
                             <option value="streamable-http">Streamable HTTP</option>
                             <option value="stdio" disabled>stdio (Local Only)</option>
                        </select>
                        <input type="text" name="mcp_url" placeholder="URL: http://..." required class="w-full bg-[#0a0a0a] border border-[#3a3a3a] rounded-lg px-3 py-2 text-xs text-zinc-200">
                        <button type="submit" class="w-full py-2 bg-blue-600 hover:bg-blue-500 text-white rounded-lg text-xs font-bold transition-all">+ Add Tools</button>
                    </form>
                </div>
            </section>
        </div>
    </div>

    <footer class="mt-8 pt-8 border-t border-[#2a2a2a] flex justify-between items-center opacity-50 text-[10px] uppercase font-bold tracking-widest text-zinc-500">
        <div>&copy; <?= date('Y') ?> Advanced AI Operations</div>
        <div>System Version: v1.4.2</div>
    </footer>

</main>

