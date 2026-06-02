<?php
require __DIR__ . '/../app/bootstrap.php';
require_admin();

$settings_saved = null;

$context_presets = [
  'ultra_cheap' => [
    'context_window_messages' => '8',
    'context_summary_enabled' => '1',
    'context_summary_max_chars' => '800',
    'context_max_message_chars' => '1200',
    'context_include_tool_traces' => '0',
  ],
  'balanced' => [
    'context_window_messages' => '14',
    'context_summary_enabled' => '1',
    'context_summary_max_chars' => '1800',
    'context_max_message_chars' => '3500',
    'context_include_tool_traces' => '0',
  ],
];

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
  $action = $_POST['action'] ?? '';
  $user_id = (int)($_POST['user_id'] ?? 0);

  if ($action === 'save_settings') {
    try {
      $allowed = [
        'llm_api_url',
        'llm_model',
        'default_llm_provider_id',
        'llm_router_enabled',
        'llm_router_trigger_name',
        'llm_router_low_model',
        'llm_router_high_model',
        'llm_router_low_provider_id',
        'llm_router_high_provider_id',
        'llm_timeout',
        'poll_interval',
        'max_tool_calls',
        'context_window_messages',
        'context_summary_enabled',
        'context_summary_max_chars',
        'context_max_message_chars',
        'context_include_tool_traces',
        'tool_permission_required',
      ];
      foreach ($allowed as $key) {
        if (isset($_POST[$key])) {
          save_setting($key, trim($_POST[$key]));
        }
      }
      $settings_saved = 'ok';
    } catch (Exception $e) {
      $settings_saved = 'error';
    }
  } elseif ($action === 'add_llm_provider') {
    try {
      $name = trim((string) ($_POST['name'] ?? ''));
      $providerType = trim((string) ($_POST['provider_type'] ?? 'openai-compatible'));
      $baseUrl = trim((string) ($_POST['base_url'] ?? ''));
      $apiKey = trim((string) ($_POST['api_key'] ?? ''));
      $defaultModel = trim((string) ($_POST['default_model'] ?? ''));
      $isActive = isset($_POST['is_active']) ? 1 : 0;

      if ($name !== '' && $baseUrl !== '') {
        if (!in_array($providerType, ['openai-compatible'], true)) {
          $providerType = 'openai-compatible';
        }

        $stmt = db()->prepare('INSERT INTO llm_providers (name, provider_type, base_url, api_key, default_model, is_active) VALUES (?, ?, ?, ?, ?, ?)');
        $stmt->execute([$name, $providerType, $baseUrl, $apiKey !== '' ? $apiKey : null, $defaultModel !== '' ? $defaultModel : null, $isActive]);

        $defaultProviderId = (int) get_setting('default_llm_provider_id', '0');
        if ($defaultProviderId <= 0) {
          save_setting('default_llm_provider_id', (string) db()->lastInsertId());
        }
      }
      redirect_to('/admin.php');
    } catch (Exception $e) {
      redirect_to('/admin.php?provider_error=1');
    }
  } elseif ($action === 'save_llm_provider') {
    try {
      $providerId = (int) ($_POST['provider_id'] ?? 0);
      $name = trim((string) ($_POST['name'] ?? ''));
      $providerType = trim((string) ($_POST['provider_type'] ?? 'openai-compatible'));
      $baseUrl = trim((string) ($_POST['base_url'] ?? ''));
      $apiKey = trim((string) ($_POST['api_key'] ?? ''));
      $defaultModel = trim((string) ($_POST['default_model'] ?? ''));
      $isActive = isset($_POST['is_active']) ? 1 : 0;

      if ($providerId > 0 && $name !== '' && $baseUrl !== '') {
        if (!in_array($providerType, ['openai-compatible'], true)) {
          $providerType = 'openai-compatible';
        }

        if ($apiKey === '') {
          $stmt = db()->prepare('SELECT api_key FROM llm_providers WHERE id = ? LIMIT 1');
          $stmt->execute([$providerId]);
          $existing = $stmt->fetch();
          $apiKey = (string) ($existing['api_key'] ?? '');
        }

        $stmt = db()->prepare('UPDATE llm_providers SET name = ?, provider_type = ?, base_url = ?, api_key = ?, default_model = ?, is_active = ? WHERE id = ?');
        $stmt->execute([$name, $providerType, $baseUrl, $apiKey !== '' ? $apiKey : null, $defaultModel !== '' ? $defaultModel : null, $isActive, $providerId]);
      }
      redirect_to('/admin.php');
    } catch (Exception $e) {
      redirect_to('/admin.php?provider_error=1');
    }
  } elseif ($action === 'toggle_llm_provider') {
    $providerId = (int) ($_POST['provider_id'] ?? 0);
    $active = isset($_POST['active']) ? 1 : 0;
    if ($providerId > 0) {
      toggle_llm_provider($providerId, (bool) $active);
    }
    redirect_to('/admin.php');
  } elseif ($action === 'delete_llm_provider') {
    $providerId = (int) ($_POST['provider_id'] ?? 0);
    if ($providerId > 0) {
      delete_llm_provider($providerId);
      $defaultProviderId = (int) get_setting('default_llm_provider_id', '0');
      if ($defaultProviderId === $providerId) {
        $remainingProviders = list_llm_providers(true);
        $next = (int) ($remainingProviders[0]['id'] ?? 0);
        save_setting('default_llm_provider_id', (string) $next);
      }
    }
    redirect_to('/admin.php');
  } elseif ($action === 'apply_context_preset') {
    try {
      $preset = trim((string)($_POST['context_preset'] ?? 'ultra_cheap'));
      $values = $context_presets[$preset] ?? $context_presets['ultra_cheap'];
      foreach ($values as $key => $value) {
        save_setting($key, $value);
      }
      $settings_saved = 'ok';
    } catch (Exception $e) {
      $settings_saved = 'error';
    }
  } elseif ($user_id > 0) {
    if ($action === 'approve') {
      $stmt = db()->prepare("UPDATE users SET status = 'approved' WHERE id = ?");
      $stmt->execute([$user_id]);
    } elseif ($action === 'reject') {
      $stmt = db()->prepare("UPDATE users SET status = 'rejected' WHERE id = ?");
      $stmt->execute([$user_id]);
    }
    redirect_to('/admin.php');
  } elseif ($action === 'add_mcp_server') {
    $name = trim($_POST['mcp_name'] ?? '');
    $type = $_POST['mcp_type'] ?? 'streamable-http';
    if ($type === 'sse') {
      $type = 'streamable-http';
    }
    $command = trim($_POST['mcp_command'] ?? '');
    $url = trim($_POST['mcp_url'] ?? '');
    if ($name && ($command || $url)) {
      add_mcp_server($name, $type, $command ?: null, $url ?: null);
    }
    redirect_to('/admin.php');
  } elseif ($action === 'delete_mcp_server') {
    $id = (int)($_POST['mcp_id'] ?? 0);
    if ($id > 0) {
      delete_mcp_server($id);
    }
    redirect_to('/admin.php');
  } elseif ($action === 'toggle_mcp_server') {
    $id = (int)($_POST['mcp_id'] ?? 0);
    $active = (bool)($_POST['active'] ?? false);
    if ($id > 0) {
      toggle_mcp_server($id, $active);
    }
    redirect_to('/admin.php');
  }
}

$stmt = db()->prepare("SELECT id, username, created_at, status FROM users WHERE status = 'pending' ORDER BY created_at DESC");
$stmt->execute();
$pending_users = $stmt->fetchAll();

$settings = get_all_settings();
$llm_providers = list_llm_providers(false);
$provider_stats = [];
try {
  $stmt = db()->query('
    SELECT llm_provider_id, COUNT(*) AS job_count, MAX(created_at) AS last_used_at
    FROM jobs
    WHERE llm_provider_id IS NOT NULL
    GROUP BY llm_provider_id
  ');
  foreach ($stmt->fetchAll() ?: [] as $row) {
    $providerStatsId = (int) ($row['llm_provider_id'] ?? 0);
    if ($providerStatsId > 0) {
      $provider_stats[$providerStatsId] = [
        'job_count' => (int) ($row['job_count'] ?? 0),
        'last_used_at' => (string) ($row['last_used_at'] ?? ''),
      ];
    }
  }
} catch (Exception $e) {
  $provider_stats = [];
}
$mcp_servers = list_mcp_servers();

$title = 'Admin Dashboard';
$stylesheet = '/assets/css/app.css';

$user = current_user();
$userId = (int) $user['id'];
$conversations = list_user_conversations($userId);

require __DIR__ . '/../app/views/head.php';
?>
<body class="flex h-[100dvh] overflow-hidden text-zinc-200 font-sans antialiased selection:bg-blue-500/30">
<?php require __DIR__ . '/../app/views/sidebar.php'; ?>
<div class="flex-1 overflow-y-auto custom-scrollbar">
<?php require __DIR__ . '/../app/views/admin_dashboard.php'; ?>
</div>
<?php $appJsVersion = @filemtime(__DIR__ . '/assets/js/app.js') ?: time(); ?>
<script src="/assets/js/app.js?v=<?= $appJsVersion ?>"></script>
</body>
</html>
