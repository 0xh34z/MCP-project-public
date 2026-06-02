<?php
/** @var string $error */
?>
<body class="bg-[#0d0d0d] text-[#f3f3f3] min-h-screen flex items-center justify-center font-sans antialiased selection:bg-blue-500/30">
  <main class="w-full max-w-md p-6">
    <section class="bg-[#151515] border border-[#2a2a2a] rounded-2xl shadow-2xl overflow-hidden backdrop-blur-sm">
      <div class="p-8 pb-6 text-center border-b border-[#2a2a2a]/50">
        <div class="mx-auto w-12 h-12 bg-[#2f2f2f] rounded-xl flex items-center justify-center mb-4 shadow-sm">
          <i class="ph ph-lock-key text-2xl text-zinc-300"></i>
        </div>
        <p class="text-xs font-semibold text-zinc-500 uppercase tracking-widest mb-2">Protected Access</p>
        <h1 class="text-2xl font-semibold m-0 text-white">PHP Worker Console</h1>
        <p class="text-sm text-zinc-400 mt-2">Authenticate to access the local control plane for conversations, jobs and remote LLM execution.</p>
      </div>
      
      <form class="p-8 flex flex-col gap-4" method="post">
        <?php if ($success ?? false): ?>
          <div class="bg-green-500/10 border border-green-500/20 text-green-400 px-4 py-3 rounded-xl text-sm"><?= htmlspecialchars($success) ?></div>
        <?php endif; ?>
        <?php if ($error): ?>
          <div class="bg-red-500/10 border border-red-500/20 text-red-400 px-4 py-3 rounded-xl text-sm flex items-center gap-2">
            <i class="ph ph-warning-circle text-lg"></i>
            <?= htmlspecialchars($error) ?>
          </div>
        <?php endif; ?>
        
        <div class="flex flex-col gap-1.5">
          <label for="username" class="text-xs font-medium text-zinc-400 ml-1">Username</label>
          <input id="username" name="username" type="text" required autocomplete="username" class="w-full bg-[#0a0a0a] border border-[#3a3a3a] rounded-xl px-4 py-2.5 text-zinc-200 focus:outline-none focus:ring-1 focus:ring-blue-500 focus:border-blue-500 transition-all placeholder-zinc-600 shadow-inner" placeholder="Enter your username">
        </div>
        
        <div class="flex flex-col gap-1.5">
          <label for="password" class="text-xs font-medium text-zinc-400 ml-1">Password</label>
          <input id="password" name="password" type="password" required autocomplete="current-password" class="w-full bg-[#0a0a0a] border border-[#3a3a3a] rounded-xl px-4 py-2.5 text-zinc-200 focus:outline-none focus:ring-1 focus:ring-blue-500 focus:border-blue-500 transition-all placeholder-zinc-600 shadow-inner" placeholder="••••••••">
        </div>
        
        <button type="submit" class="w-full bg-white text-zinc-900 hover:bg-zinc-200 font-medium py-2.5 rounded-xl transition-colors mt-2">Sign In</button>
      </form>
      
      <div class="px-8 pb-8 text-center text-sm">
        <a href="/register.php" class="text-blue-400 hover:text-blue-300 transition-colors">Don't have an account? Register</a>
      </div>
    </section>
  </main>
</body>
</html>
