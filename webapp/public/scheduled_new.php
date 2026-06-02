<?php
require __DIR__ . '/../app/bootstrap.php';

require_auth();

$user = current_user();
$userId = (int) $user['id'];
$action = $_GET['action'] ?? $_POST['action'] ?? '';

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
  if ($action === 'schedule_prompt') {
    $prompt = trim($_POST['prompt'] ?? '');
    $scheduledAt = $_POST['scheduled_at'] ?? null;
    $conversationId = (int) ($_POST['conversation_id'] ?? 0);
    $repeatCount = (int) ($_POST['repeat_count'] ?? 1);
    $repeatInterval = (int) ($_POST['repeat_interval'] ?? 0);
    $llmModel = trim($_POST['llm_model'] ?? '') ?: null;
    $llmApiUrl = trim($_POST['llm_api_url'] ?? '') ?: null;
    $mcpServers = isset($_POST['mcp_servers']) ? implode(',', $_POST['mcp_servers']) : null;
    $autoApproveTools = isset($_POST['auto_approve_tools']) ? 1 : 0;
    
    if ($prompt) {
      if (!$conversationId) {
        $conversationId = create_user_conversation($userId, 'Scheduled: ' . substr($prompt, 0, 20));
      }
      enqueue_conversation_message(
        $conversationId, $userId, $prompt, $scheduledAt, 
        $repeatCount, $repeatInterval, null, $llmModel, $llmApiUrl, $mcpServers, $autoApproveTools
      );
      redirect_to('/scheduled.php?success=1');
    }
  }
}

$conversations = list_user_conversations($userId);
$mcp_servers = list_mcp_servers();
$settings = get_all_settings();

$title = 'New Scheduled Prompt';
$stylesheet = '/assets/css/app.css';

require __DIR__ . '/../app/views/head.php';
?>
<body class="flex h-[100dvh] overflow-hidden text-zinc-200 font-sans antialiased selection:bg-blue-500/30">
<?php require __DIR__ . '/../app/views/sidebar.php'; ?>
<div class="flex-1 overflow-y-auto custom-scrollbar">
<?php require __DIR__ . '/../app/views/scheduled_new.php'; ?>
</div>
<?php $appJsVersion = @filemtime(__DIR__ . '/assets/js/app.js') ?: time(); ?>
<script src="/assets/js/app.js?v=<?= $appJsVersion ?>"></script>
</body>
</html>
