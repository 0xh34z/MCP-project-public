<?php
require __DIR__ . '/../app/bootstrap.php';
require_auth();

$user = current_user();
$userId = (int) $user['id'];

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
  $action = $_POST['action'] ?? '';
  
  if ($action === 'delete_job') {
    $jobId = (int) ($_POST['job_id'] ?? 0);
    if ($jobId > 0) {
      delete_job($jobId, $userId);
    }
    redirect_to('/scheduled.php?deleted=1');
  }
}

$scheduled_jobs = list_scheduled_jobs($userId);
$conversations = list_user_conversations($userId);
$mcp_servers = list_mcp_servers();

$title = 'Scheduled Prompts';
$stylesheet = '/assets/css/app.css';

require __DIR__ . '/../app/views/head.php';
?>
<body class="flex h-[100dvh] overflow-hidden text-zinc-200 font-sans antialiased selection:bg-blue-500/30">
<?php require __DIR__ . '/../app/views/sidebar.php'; ?>
<div class="flex-1 overflow-y-auto custom-scrollbar">
<?php require __DIR__ . '/../app/views/scheduled_prompts.php'; ?>
</div>
<?php $appJsVersion = @filemtime(__DIR__ . '/assets/js/app.js') ?: time(); ?>
<script src="/assets/js/app.js?v=<?= $appJsVersion ?>"></script>
</body>
</html>
