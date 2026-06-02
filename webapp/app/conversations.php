<?php
function find_user_by_username(string $username): array|false {
  $stmt = db()->prepare('SELECT id, username, password_hash, role, status FROM users WHERE username = ?');
  $stmt->execute([$username]);
  return $stmt->fetch();
}

function strip_transport_tags(?string $value): string {
  $text = (string) ($value ?? '');
  $text = preg_replace('/\[(?:\/?\s*text\s*|\s*text\s*\/)\]/i', '', $text) ?? $text;
  return trim($text);
}

function ensure_application_schema(): void {
  try {
    $pdo = db();

    $pdo->exec(
      "CREATE TABLE IF NOT EXISTS llm_providers (
        id INT AUTO_INCREMENT PRIMARY KEY,
        name VARCHAR(128) NOT NULL,
        provider_type ENUM('openai-compatible') NOT NULL DEFAULT 'openai-compatible',
        base_url VARCHAR(255) NOT NULL,
        api_key TEXT NULL,
        default_model VARCHAR(255) NULL,
        is_active BOOLEAN DEFAULT TRUE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        INDEX idx_llm_providers_active (is_active)
      )"
    );

    $pdo->exec(
      "CREATE TABLE IF NOT EXISTS chat_message_feedback (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        message_id INT NOT NULL,
        conversation_id INT NOT NULL,
        user_id INT NOT NULL,
        reaction ENUM('up', 'down') NOT NULL,
        note TEXT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        UNIQUE KEY uniq_message_user_feedback (message_id, user_id),
        INDEX idx_chat_feedback_created_at (created_at),
        INDEX idx_chat_feedback_reaction (reaction),
        FOREIGN KEY (message_id) REFERENCES messages(id) ON DELETE CASCADE,
        FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
      )"
    );

    $stmt = $pdo->query("SHOW COLUMNS FROM jobs LIKE 'llm_provider_id'");
    if ($stmt->fetch() === false) {
      $pdo->exec("ALTER TABLE jobs ADD COLUMN llm_provider_id INT NULL AFTER repeat_interval");
    }

    $stmt = $pdo->query("SHOW COLUMNS FROM jobs LIKE 'llm_api_url'");
    if ($stmt->fetch() === false) {
      $pdo->exec("ALTER TABLE jobs ADD COLUMN llm_api_url VARCHAR(255) NULL AFTER llm_model");
    }

    $stmt = $pdo->query("SHOW COLUMNS FROM jobs LIKE 'mcp_servers'");
    if ($stmt->fetch() === false) {
      $pdo->exec("ALTER TABLE jobs ADD COLUMN mcp_servers TEXT NULL AFTER llm_api_url");
    }

    $stmt = $pdo->query("SHOW COLUMNS FROM jobs LIKE 'auto_approve_tools'");
    if ($stmt->fetch() === false) {
      $pdo->exec("ALTER TABLE jobs ADD COLUMN auto_approve_tools TINYINT(1) NULL DEFAULT NULL AFTER mcp_servers");
    }

    $stmt = $pdo->query("SHOW COLUMNS FROM messages LIKE 'llm_provider_id'");
    if ($stmt->fetch() === false) {
      $pdo->exec("ALTER TABLE messages ADD COLUMN llm_provider_id INT NULL AFTER content");
    }

    $stmt = $pdo->query("SHOW COLUMNS FROM messages LIKE 'llm_model'");
    if ($stmt->fetch() === false) {
      $pdo->exec("ALTER TABLE messages ADD COLUMN llm_model VARCHAR(255) NULL AFTER llm_provider_id");
    }

    $stmt = $pdo->query("SHOW COLUMNS FROM messages LIKE 'updated_at'");
    if ($stmt->fetch() === false) {
      $pdo->exec("ALTER TABLE messages ADD COLUMN updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP AFTER created_at");
    }

    $stmt = $pdo->query("SHOW INDEX FROM messages WHERE Key_name = 'idx_messages_conversation_id_id'");
    if ($stmt->fetch() === false) {
      $pdo->exec("CREATE INDEX idx_messages_conversation_id_id ON messages (conversation_id, id)");
    }

    $stmt = $pdo->query("SHOW INDEX FROM messages WHERE Key_name = 'idx_messages_conversation_id_updated_at'");
    if ($stmt->fetch() === false) {
      $pdo->exec("CREATE INDEX idx_messages_conversation_id_updated_at ON messages (conversation_id, updated_at)");
    }

    $stmt = $pdo->query("SHOW INDEX FROM jobs WHERE Key_name = 'idx_jobs_conversation_status'");
    if ($stmt->fetch() === false) {
      $pdo->exec("CREATE INDEX idx_jobs_conversation_status ON jobs (conversation_id, status, scheduled_at, id)");
    }

    $stmt = $pdo->query("SHOW INDEX FROM tool_approvals WHERE Key_name = 'idx_tool_approvals_conversation_status'");
    if ($stmt->fetch() === false) {
      $pdo->exec("CREATE INDEX idx_tool_approvals_conversation_status ON tool_approvals (conversation_id, status, id)");
    }

    $stmt = $pdo->query("SHOW INDEX FROM clarification_requests WHERE Key_name = 'idx_clarification_requests_conversation_status'");
    if ($stmt->fetch() === false) {
      $pdo->exec("CREATE INDEX idx_clarification_requests_conversation_status ON clarification_requests (conversation_id, status, id)");
    }


    $stmt = $pdo->prepare('SELECT id FROM llm_providers WHERE base_url = ? LIMIT 1');
    $stmt->execute(['http://192.168.1.196:8080']);
    if ($stmt->fetch() === false) {
      $stmt = $pdo->prepare('INSERT INTO llm_providers (name, provider_type, base_url, default_model, is_active) VALUES (?, ?, ?, ?, 1)');
      $stmt->execute(['Llama.cpp Router', 'openai-compatible', 'http://192.168.1.196:8080', 'Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive-Q4_K_M']);
    }

    $stmt = $pdo->prepare('SELECT id, default_model FROM llm_providers WHERE name = ? LIMIT 1');
    $stmt->execute(['Llama.cpp Router']);
    $llamaRow = $stmt->fetch();
    $llamaId = $llamaRow ? (int) $llamaRow['id'] : 0;
    $llamaModel = $llamaRow ? trim((string) ($llamaRow['default_model'] ?? '')) : '';

    $currentDefault = (int) get_setting('default_llm_provider_id', '0');

    if ($llamaId > 0 && $currentDefault === 0) {
      $pdo->prepare('INSERT INTO settings (`key`, `value`) VALUES (?, ?) ON DUPLICATE KEY UPDATE `value` = VALUES(`value`)')->execute(['default_llm_provider_id', (string) $llamaId]);
      // Set the default llm_model explicitly to DeepSeek v4 flash for fresh installs
      $pdo->prepare('INSERT INTO settings (`key`, `value`) VALUES (?, ?) ON DUPLICATE KEY UPDATE `value` = VALUES(`value`)')->execute(['llm_model', 'deepseek/deepseek-v4-flash']);
    }

    // OpenRouter provider management is handled by the installation schema (schema.sql) for clean installs.
    
    // Optimize poll interval setting to 0.2s for near-instant job pickup
    $stmt = $pdo->prepare("UPDATE settings SET value = '0.2' WHERE `key` = 'poll_interval' AND `value` = '2'");
    $stmt->execute();

    // Clean up existing large base64 attachments in the messages table
    $stmt = $pdo->query("SELECT id, content FROM messages WHERE role = 'user' AND content LIKE '%encoding=\"base64\"%'");
    while ($row = $stmt->fetch()) {
      $stripped = strip_binary_attachments($row['content']);
      if ($stripped !== $row['content']) {
        $updateStmt = $pdo->prepare('UPDATE messages SET content = ? WHERE id = ?');
        $updateStmt->execute([$stripped, $row['id']]);
      }
    }
  } catch (Throwable $e) {
    error_log('Schema ensure skipped: ' . $e->getMessage());
  }
}

function list_llm_providers(bool $activeOnly = true): array {
  $sql = 'SELECT id, name, provider_type, base_url, api_key, default_model, is_active, created_at, updated_at FROM llm_providers';
  if ($activeOnly) {
    $sql .= ' WHERE is_active = 1';
  }
  $sql .= ' ORDER BY is_active DESC, created_at DESC, name ASC';
  $stmt = db()->query($sql);
  return $stmt->fetchAll() ?: [];
}

function get_llm_provider_by_id(int $providerId): array|false {
  $stmt = db()->prepare('SELECT id, name, provider_type, base_url, api_key, default_model, is_active, created_at, updated_at FROM llm_providers WHERE id = ? LIMIT 1');
  $stmt->execute([$providerId]);
  return $stmt->fetch();
}

function get_default_llm_provider(): array|false {
  $defaultId = (int) get_setting('default_llm_provider_id', '0');
  if ($defaultId > 0) {
    $provider = get_llm_provider_by_id($defaultId);
    if ($provider) {
      return $provider;
    }
  }

  $providers = list_llm_providers(true);
  return $providers[0] ?? false;
}

function get_conversation_llm_selection(int $conversationId): array {
  $stmt = db()->prepare(
    'SELECT j.llm_provider_id, j.llm_model, j.llm_api_url, lp.name AS provider_name, lp.provider_type, lp.base_url, lp.default_model
     FROM jobs j
     LEFT JOIN llm_providers lp ON lp.id = j.llm_provider_id
     WHERE j.conversation_id = ?
     ORDER BY j.id DESC
     LIMIT 1'
  );
  $stmt->execute([$conversationId]);
  $row = $stmt->fetch();

  if ($row) {
    return [
      'provider_id' => (int) ($row['llm_provider_id'] ?? 0),
      'provider_name' => trim((string) ($row['provider_name'] ?? '')) ?: 'Legacy Provider',
      'provider_type' => trim((string) ($row['provider_type'] ?? '')) ?: 'openai-compatible',
      'provider_url' => trim((string) ($row['base_url'] ?? $row['llm_api_url'] ?? '')),
      'model' => trim((string) ($row['llm_model'] ?? '')),
    ];
  }

  $provider = get_default_llm_provider();
  return [
    'provider_id' => $provider ? (int) $provider['id'] : 0,
    'provider_name' => $provider ? trim((string) ($provider['name'] ?? 'Default Provider')) : 'Default Provider',
    'provider_type' => $provider ? trim((string) ($provider['provider_type'] ?? 'openai-compatible')) : 'openai-compatible',
    'provider_url' => $provider ? trim((string) ($provider['base_url'] ?? '')) : get_setting('llm_api_url', ''),
    'model' => $provider ? trim((string) ($provider['default_model'] ?? '')) : get_setting('llm_model', 'llama3'),
  ];
}

function fetch_llm_provider_models(array $provider): array {
  $baseUrl = rtrim(trim((string) ($provider['base_url'] ?? '')), '/');
  if ($baseUrl === '') {
    return [];
  }

  $providerType = trim((string) ($provider['provider_type'] ?? 'openai-compatible'));
  $endpoint = '/v1/models';
  if (preg_match('~/v1$~i', $baseUrl)) {
    $endpoint = '/models';
  }
  $headers = ["Accept: application/json"];
  $apiKey = trim((string) ($provider['api_key'] ?? ''));
  if ($apiKey !== '') {
    $headers[] = 'Authorization: Bearer ' . $apiKey;
  }

  $context = stream_context_create([
    'http' => [
      'timeout' => 4,
      'ignore_errors' => true,
      'header' => implode("\r\n", $headers),
    ],
  ]);

  $raw = @file_get_contents($baseUrl . $endpoint, false, $context);
  if ($raw === false || $raw === '') {
    return [];
  }

  $payload = json_decode($raw, true);
  if (!is_array($payload)) {
    return [];
  }

  $models = [];
  $loadedMap = [];

  $slotsRaw = @file_get_contents($baseUrl . '/slots', false, $context);
  if ($slotsRaw !== false && $slotsRaw !== '') {
    $slotsPayload = json_decode($slotsRaw, true);
    if (is_array($slotsPayload)) {
      foreach ($slotsPayload as $slot) {
        if (!is_array($slot)) {
          continue;
        }
        $slotModel = trim((string) ($slot['model'] ?? $slot['model_name'] ?? $slot['filename'] ?? ''));
        if ($slotModel !== '') {
          $loadedMap[strtolower($slotModel)] = true;
        }
      }
    }
  }

  $registerModel = static function (array &$bucket, array $entry): void {
    $name = trim((string) ($entry['name'] ?? ''));
    if ($name === '') {
      return;
    }

    $key = strtolower($name);
    if (!isset($bucket[$key])) {
      $bucket[$key] = [
        'name' => $name,
        'loaded' => $entry['loaded'] ?? null,
      ];
      return;
    }

    if (($entry['loaded'] ?? null) === true) {
      $bucket[$key]['loaded'] = true;
    } elseif (($entry['loaded'] ?? null) === false && $bucket[$key]['loaded'] === null) {
      $bucket[$key]['loaded'] = false;
    }
  };


  foreach (($payload['data'] ?? []) as $model) {
    $name = trim((string) ($model['id'] ?? $model['name'] ?? ''));
    if ($name !== '') {
      $lower = strtolower($name);
      $loaded = null;
      if (isset($loadedMap[$lower])) {
        $loaded = true;
      } else {
        foreach ($loadedMap as $loadedName => $loadedFlag) {
          if (str_contains($loadedName, $lower) || str_contains($lower, $loadedName)) {
            $loaded = true;
            break;
          }
        }
      }
      $registerModel($models, [
        'name' => $name,
        'loaded' => $loaded,
      ]);
    }
  }

  return array_values($models);
}

function list_user_conversations(int $userId): array {
  $stmt = db()->prepare('
    SELECT c.id, c.title, c.updated_at,
           (SELECT content FROM messages WHERE conversation_id = c.id AND role = "user" ORDER BY id LIMIT 1) as snippet
    FROM conversations c 
    WHERE c.user_id = ? 
    ORDER BY c.updated_at DESC
  ');
  $stmt->execute([$userId]);
  $rows = $stmt->fetchAll() ?: [];
  foreach ($rows as &$row) {
    $row['title'] = strip_transport_tags($row['title'] ?? '');
    $row['snippet'] = strip_transport_tags($row['snippet'] ?? '');
  }
  unset($row);
  return $rows;
}

function create_user_conversation(int $userId, ?string $title = null): int {
  $title = $title ?: 'New Conversation ' . date('H:i:s');
  $stmt = db()->prepare('INSERT INTO conversations (user_id, title) VALUES (?, ?)');
  $stmt->execute([$userId, $title]);
  return (int) db()->lastInsertId();
}

function user_owns_conversation(int $conversationId, int $userId): bool {
  $stmt = db()->prepare('SELECT id FROM conversations WHERE id = ? AND user_id = ?');
  $stmt->execute([$conversationId, $userId]);
  return (bool) $stmt->fetch();
}

function require_conversation_ownership(int $conversationId, int $userId): void {
  if (!user_owns_conversation($conversationId, $userId)) {
    json_response(['error' => 'forbidden'], 403);
  }
}

function list_conversation_messages(int $conversationId, ?int $userId = null): array {
  if ($userId !== null) {
    $stmt = db()->prepare(
      'SELECT m.id, m.role, m.content, m.created_at, m.llm_provider_id, m.llm_model, lp.name AS llm_provider_name, cf.id AS feedback_id, cf.reaction AS feedback_reaction
       FROM messages m
       LEFT JOIN llm_providers lp ON lp.id = m.llm_provider_id
       LEFT JOIN chat_message_feedback cf ON cf.message_id = m.id AND cf.user_id = ?
       WHERE m.conversation_id = ?
       ORDER BY m.id ASC'
    );
    $stmt->execute([$userId, $conversationId]);
    return $stmt->fetchAll() ?: [];
  }

  $stmt = db()->prepare('SELECT m.id, m.role, m.content, m.created_at, m.llm_provider_id, m.llm_model, lp.name AS llm_provider_name FROM messages m LEFT JOIN llm_providers lp ON lp.id = m.llm_provider_id WHERE m.conversation_id = ? ORDER BY m.id ASC');
  $stmt->execute([$conversationId]);
  return $stmt->fetchAll() ?: [];
}

function count_pending_jobs(int $conversationId): int {
  $stmt = db()->prepare("SELECT COUNT(*) AS c FROM jobs WHERE conversation_id = ? AND status IN ('pending', 'running')");
  $stmt->execute([$conversationId]);
  $row = $stmt->fetch();
  return (int) ($row['c'] ?? 0);
}

function get_next_scheduled_at(int $conversationId): ?int {
  $stmt = db()->prepare("SELECT UNIX_TIMESTAMP(MIN(scheduled_at)) AS n FROM jobs WHERE conversation_id = ? AND status IN ('pending', 'running')");
  $stmt->execute([$conversationId]);
  $row = $stmt->fetch();
  return $row['n'] !== null ? (int)$row['n'] : null;
}

function has_active_auto_approve_job(int $conversationId): bool {
  $stmt = db()->prepare(
    "SELECT 1 FROM jobs
     WHERE conversation_id = ?
       AND status IN ('pending', 'running')
       AND COALESCE(auto_approve_tools, 0) = 1
       AND (status = 'running' OR scheduled_at <= NOW())
     LIMIT 1"
  );
  $stmt->execute([$conversationId]);
  return (bool) $stmt->fetch();
}

function get_active_mcp_override_servers(int $conversationId): ?array {
  $stmt = db()->prepare(
    "SELECT mcp_servers
     FROM jobs
     WHERE conversation_id = ?
       AND status IN ('pending', 'running')
       AND mcp_servers IS NOT NULL
       AND TRIM(mcp_servers) <> ''
       AND (status = 'running' OR scheduled_at <= NOW())
     ORDER BY (status = 'running') DESC, id ASC
     LIMIT 1"
  );
  $stmt->execute([$conversationId]);
  $row = $stmt->fetch();
  if (!$row) {
    return null;
  }

  $csv = trim((string)($row['mcp_servers'] ?? ''));
  if ($csv === '') {
    return null;
  }

  $servers = array_values(array_filter(array_map(
    static fn(string $v): string => strtolower(trim($v)),
    explode(',', $csv)
  )));

  if (empty($servers)) {
    return null;
  }

  return array_values(array_unique($servers));
}

function get_active_mcp_override_hash(int $conversationId): string {
  $servers = get_active_mcp_override_servers($conversationId);
  if ($servers === null) {
    return '';
  }
  sort($servers);
  return md5(json_encode($servers));
}

function strip_binary_attachments(string $message): string {
  return preg_replace_callback(
    '/\[ATTACHMENT([^\]]*?(?:encoding="base64"|binary="1")[^\]]*?)\]([\s\S]*?)\[\/ATTACHMENT\]/i',
    function ($matches) {
      return '[ATTACHMENT' . $matches[1] . "]\n[Binary content omitted]\n[/ATTACHMENT]";
    },
    $message
  ) ?? $message;
}

function enqueue_conversation_message(
  int $conversationId, 
  int $userId, 
  string $message, 
  ?string $scheduledAt = null,
  int $repeatCount = 1,
  int $repeatInterval = 0,
  ?int $llmProviderId = null,
  ?string $llmModel = null,
  ?string $llmApiUrl = null,
  ?string $mcpServers = null,
  ?int $autoApproveTools = null
): void {
  $pdo = db();
  $role = 'user';
  $strippedMessage = strip_binary_attachments($message);
  $pdo->prepare('INSERT INTO messages (conversation_id, role, content) VALUES (?, ?, ?)')->execute([$conversationId, $role, $strippedMessage]);
  
  $sql = "INSERT INTO jobs (conversation_id, user_id, prompt, status, scheduled_at, repeat_count, repeat_interval, llm_provider_id, llm_model, llm_api_url, mcp_servers, auto_approve_tools) 
          VALUES (?, ?, ?, 'pending', COALESCE(?, CURRENT_TIMESTAMP), ?, ?, ?, ?, ?, ?, ?)";
  $pdo->prepare($sql)->execute([
    $conversationId, $userId, $message, $scheduledAt, 
    $repeatCount, $repeatInterval, $llmProviderId, $llmModel, $llmApiUrl, $mcpServers, $autoApproveTools
  ]);
}

function upsert_message_feedback(int $messageId, int $conversationId, int $userId, string $reaction, ?string $note = null): void {
  $stmt = db()->prepare(
    'INSERT INTO chat_message_feedback (message_id, conversation_id, user_id, reaction, note)
     VALUES (?, ?, ?, ?, ?)
     ON DUPLICATE KEY UPDATE reaction = VALUES(reaction), note = VALUES(note), updated_at = CURRENT_TIMESTAMP'
  );
  $stmt->execute([$messageId, $conversationId, $userId, $reaction, $note]);
}

function clear_message_feedback(int $messageId, int $conversationId, int $userId): void {
  $stmt = db()->prepare('DELETE FROM chat_message_feedback WHERE message_id = ? AND conversation_id = ? AND user_id = ?');
  $stmt->execute([$messageId, $conversationId, $userId]);
}

function list_message_feedback(int $conversationId, int $userId): array {
  $stmt = db()->prepare('SELECT message_id, reaction FROM chat_message_feedback WHERE conversation_id = ? AND user_id = ?');
  $stmt->execute([$conversationId, $userId]);
  $rows = $stmt->fetchAll() ?: [];
  $feedback = [];
  foreach ($rows as $row) {
    $feedback[(int) ($row['message_id'] ?? 0)] = (string) ($row['reaction'] ?? '');
  }
  return $feedback;
}

function list_scheduled_jobs(int $userId): array {
  $stmt = db()->prepare("
    SELECT j.*, c.title as conversation_title 
    FROM jobs j 
    JOIN conversations c ON j.conversation_id = c.id 
    WHERE j.user_id = ? AND j.status = 'pending' AND j.scheduled_at > NOW() 
    ORDER BY j.scheduled_at ASC
  ");
  $stmt->execute([$userId]);
  return $stmt->fetchAll() ?: [];
}

function delete_job(int $jobId, int $userId): void {
  $stmt = db()->prepare("DELETE FROM jobs WHERE id = ? AND user_id = ? AND status = 'pending'");
  $stmt->execute([$jobId, $userId]);
}

function get_setting(string $key, string $default = ''): string {
  $stmt = db()->prepare('SELECT `value` FROM settings WHERE `key` = ?');
  $stmt->execute([$key]);
  $row = $stmt->fetch();
  return $row ? $row['value'] : $default;
}

function get_all_settings(): array {
  $stmt = db()->query('SELECT `key`, `value` FROM settings');
  $result = [];
  foreach ($stmt->fetchAll() as $row) {
    $result[$row['key']] = $row['value'];
  }
  return $result;
}

function save_setting(string $key, string $value): void {
  $stmt = db()->prepare('INSERT INTO settings (`key`, `value`) VALUES (?, ?) ON DUPLICATE KEY UPDATE `value` = VALUES(`value`)');
  $stmt->execute([$key, $value]);
}

function list_mcp_servers(): array {
  $stmt = db()->query('SELECT * FROM mcp_servers ORDER BY created_at DESC');
  return $stmt->fetchAll() ?: [];
}

function add_mcp_server(string $name, string $type, ?string $command, ?string $url): void {
  $stmt = db()->prepare('INSERT INTO mcp_servers (name, type, command, url) VALUES (?, ?, ?, ?)');
  $stmt->execute([$name, $type, $command, $url]);
}

function delete_llm_provider(int $id): void {
  $stmt = db()->prepare('DELETE FROM llm_providers WHERE id = ?');
  $stmt->execute([$id]);
}

function toggle_llm_provider(int $id, bool $active): void {
  $stmt = db()->prepare('UPDATE llm_providers SET is_active = ? WHERE id = ?');
  $stmt->execute([(int) $active, $id]);
}

function delete_mcp_server(int $id): void {
  $stmt = db()->prepare('DELETE FROM mcp_servers WHERE id = ?');
  $stmt->execute([$id]);
}

function toggle_mcp_server(int $id, bool $active): void {
  $stmt = db()->prepare('UPDATE mcp_servers SET is_active = ? WHERE id = ?');
  $stmt->execute([(int)$active, $id]);
}

function get_user_profile(int $userId): array {
  $stmt = db()->prepare('SELECT persona, blueprints FROM users WHERE id = ?');
  $stmt->execute([$userId]);
  return $stmt->fetch() ?: ['persona' => '', 'blueprints' => ''];
}

function update_user_profile(int $userId, string $persona, string $blueprints): void {
  $stmt = db()->prepare('UPDATE users SET persona = ?, blueprints = ? WHERE id = ?');
  $stmt->execute([$persona, $blueprints, $userId]);
}

function delete_user_conversation(int $conversationId): void {
  $stmt = db()->prepare('DELETE FROM conversations WHERE id = ?');
  $stmt->execute([$conversationId]);
}

function get_pending_tool_approvals(int $conversationId): array {
  if ($conversationId > 0) {
    $stmt = db()->prepare("SELECT id, job_id, conversation_id, tool_name, server_name, arguments_json, status, created_at FROM tool_approvals WHERE conversation_id = ? AND status = 'pending' ORDER BY id ASC");
    $stmt->execute([$conversationId]);
  } else {
    $stmt = db()->query("SELECT id, job_id, conversation_id, tool_name, server_name, arguments_json, status, created_at FROM tool_approvals WHERE status = 'pending' ORDER BY id ASC");
  }
  return $stmt->fetchAll() ?: [];
}

function resolve_tool_approval(int $approvalId, int $userId, string $decision): void {
  $stmt = db()->prepare("UPDATE tool_approvals SET status = ? WHERE id = ? AND user_id = ?");
  $stmt->execute([$decision, $approvalId, $userId]);
}

function create_clarification_request(
  int $jobId,
  int $conversationId,
  int $userId,
  string $question,
  ?string $detailsJson = null
): int {
  $stmt = db()->prepare(
    'INSERT INTO clarification_requests (job_id, conversation_id, user_id, question, details_json, status) VALUES (?, ?, ?, ?, ?, "pending")'
  );
  $stmt->execute([$jobId, $conversationId, $userId, $question, $detailsJson]);
  return (int) db()->lastInsertId();
}

function get_pending_clarification_requests(int $conversationId): array {
  if ($conversationId > 0) {
    $stmt = db()->prepare(
      "SELECT id, job_id, conversation_id, user_id, question, details_json, answer_text, status, created_at, updated_at
       FROM clarification_requests
       WHERE conversation_id = ? AND status = 'pending'
       ORDER BY id ASC"
    );
    $stmt->execute([$conversationId]);
  } else {
    $stmt = db()->query(
      "SELECT id, job_id, conversation_id, user_id, question, details_json, answer_text, status, created_at, updated_at
       FROM clarification_requests
       WHERE status = 'pending'
       ORDER BY id ASC"
    );
  }
  return $stmt->fetchAll() ?: [];
}

function resolve_clarification_request(int $requestId, int $userId, string $answerText): void {
  $stmt = db()->prepare(
    "UPDATE clarification_requests
     SET status = 'answered', answer_text = ?, updated_at = CURRENT_TIMESTAMP
     WHERE id = ? AND user_id = ? AND status = 'pending'"
  );
  $stmt->execute([$answerText, $requestId, $userId]);
}

function terminate_conversation_jobs(int $conversationId, int $userId): int {
  $pdo = db();

  $stmt = $pdo->prepare(
    "UPDATE jobs j
     INNER JOIN conversations c ON c.id = j.conversation_id
     SET j.status = 'error',
         j.error_text = CASE
           WHEN j.error_text IS NULL OR TRIM(j.error_text) = '' THEN 'Terminated by user'
           ELSE j.error_text
         END
     WHERE j.conversation_id = ?
       AND c.user_id = ?
       AND j.status IN ('pending', 'running')"
  );
  $stmt->execute([$conversationId, $userId]);
  $affectedJobs = (int) $stmt->rowCount();

  $stmt = $pdo->prepare(
    "UPDATE tool_approvals
     SET status = 'denied'
     WHERE conversation_id = ?
       AND user_id = ?
       AND status = 'pending'"
  );
  $stmt->execute([$conversationId, $userId]);

  $stmt = $pdo->prepare(
    "UPDATE clarification_requests
     SET status = 'closed', updated_at = CURRENT_TIMESTAMP
     WHERE conversation_id = ?
       AND user_id = ?
       AND status = 'pending'"
  );
  $stmt->execute([$conversationId, $userId]);

  return $affectedJobs;
}

function get_clarification_request_by_id(int $requestId, int $userId): array|false {
  $stmt = db()->prepare(
    'SELECT id, job_id, conversation_id, user_id, question, details_json, answer_text, status, created_at, updated_at
     FROM clarification_requests
     WHERE id = ? AND user_id = ?
     LIMIT 1'
  );
  $stmt->execute([$requestId, $userId]);
  return $stmt->fetch();
}
