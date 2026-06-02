<?php
require __DIR__ . '/../app/bootstrap.php';

require_auth();

$user = current_user();
$conversations = list_user_conversations((int) $user['id']);
$title = 'GUI Console';
$stylesheet = '/assets/css/app.css';

require __DIR__ . '/../app/views/head.php';
?>
<body class="flex h-[100dvh] overflow-hidden text-zinc-200 font-sans antialiased selection:bg-blue-500/30">
<?php require __DIR__ . '/../app/views/sidebar.php'; ?>
<?php require __DIR__ . '/../app/views/chat_shell.php'; ?>
<?php $appJsVersion = @filemtime(__DIR__ . '/assets/js/app.js') ?: time(); ?>
<script src="/assets/js/app.js?v=<?= $appJsVersion ?>"></script>
</body>
</html>
