<?php
require __DIR__ . '/../app/bootstrap.php';

require_auth();

$action = $_GET['action'] ?? '';
$input = json_input();
$user = current_user();
$userId = (int) $user['id'];

if ($action === 'new_conversation') {
  json_response(['id' => create_user_conversation($userId)]);
}

if ($action === 'new_conversation_redirect') {
  $id = create_user_conversation($userId);
  redirect_to('/?conversation_id=' . $id);
}

if ($action === 'messages') {
  $conversationId = (int) ($input['conversation_id'] ?? 0);
  require_conversation_ownership($conversationId, $userId);

  $lightMode = !empty($input['light']);

  if ($lightMode) {
    json_response([
      'messages' => list_conversation_messages($conversationId, $userId),
      'pending_jobs' => count_pending_jobs($conversationId),
      'next_scheduled_at' => get_next_scheduled_at($conversationId),
    ]);
  }

  json_response([
    'messages' => list_conversation_messages($conversationId, $userId),
    'pending_jobs' => count_pending_jobs($conversationId),
    'next_scheduled_at' => get_next_scheduled_at($conversationId),
    'auto_approve_active' => has_active_auto_approve_job($conversationId),
    'mcp_override_hash' => get_active_mcp_override_hash($conversationId),
    'pending_clarifications' => get_pending_clarification_requests($conversationId),
    'llm_selection' => get_conversation_llm_selection($conversationId),
    'pending_approvals' => get_pending_tool_approvals($conversationId),
  ]);
}

if ($action === 'conversation_state') {
  $conversationId = (int) ($input['conversation_id'] ?? 0);
  require_conversation_ownership($conversationId, $userId);

  json_response([
    'pending_jobs' => count_pending_jobs($conversationId),
    'next_scheduled_at' => get_next_scheduled_at($conversationId),
    'auto_approve_active' => has_active_auto_approve_job($conversationId),
    'mcp_override_hash' => get_active_mcp_override_hash($conversationId),
    'pending_clarifications' => get_pending_clarification_requests($conversationId),
    'llm_selection' => get_conversation_llm_selection($conversationId),
    'pending_approvals' => get_pending_tool_approvals($conversationId),
  ]);
}

if ($action === 'enqueue') {
  $conversationId = (int) ($input['conversation_id'] ?? 0);
  $message = trim((string) ($input['message'] ?? ''));
  $requestedProviderId = (int) ($input['llm_provider_id'] ?? 0);
  $requestedModel = trim((string) ($input['llm_model'] ?? '')) ?: null;

  if ($conversationId <= 0 || $message === '') {
    json_response(['error' => 'bad_request'], 400);
  }

  require_conversation_ownership($conversationId, $userId);

  $provider = $requestedProviderId > 0 ? get_llm_provider_by_id($requestedProviderId) : get_default_llm_provider();
  if ($provider) {
    $requestedProviderId = (int) $provider['id'];
    if ($requestedModel === null || $requestedModel === '') {
      $requestedModel = trim((string) ($provider['default_model'] ?? '')) ?: null;
    }
  }

  $providerUrl = $provider ? trim((string) ($provider['base_url'] ?? '')) : null;
  enqueue_conversation_message($conversationId, $userId, $message, null, 1, 0, $requestedProviderId ?: null, $requestedModel, $providerUrl);

  json_response(['ok' => true]);
}

if ($action === 'llm_providers') {
  $providers = array_map(static function (array $provider): array {
    unset($provider['api_key']);
    return $provider;
  }, list_llm_providers(true));
  json_response([
    'providers' => $providers,
    'default_provider_id' => (int) get_setting('default_llm_provider_id', '0'),
    'legacy' => [
      'llm_api_url' => get_setting('llm_api_url', ''),
      'llm_model' => get_setting('llm_model', ''),
    ],
  ]);
}

if ($action === 'llm_models') {
  $providerId = (int) ($input['provider_id'] ?? 0);
  if ($providerId <= 0) {
    $providerId = (int) get_setting('default_llm_provider_id', '0');
  }

  $provider = $providerId > 0 ? get_llm_provider_by_id($providerId) : false;
  if (!$provider) {
    json_response(['models' => []]);
  }

  json_response([
    'provider' => [
      'id' => (int) $provider['id'],
      'name' => $provider['name'],
      'provider_type' => $provider['provider_type'],
      'base_url' => $provider['base_url'],
      'default_model' => $provider['default_model'],
    ],
    'models' => fetch_llm_provider_models($provider),
  ]);
}

if ($action === 'terminate_run') {
  $conversationId = (int) ($input['conversation_id'] ?? 0);
  if ($conversationId <= 0) {
    json_response(['error' => 'bad_request'], 400);
  }

  require_conversation_ownership($conversationId, $userId);
  $terminated = terminate_conversation_jobs($conversationId, $userId);

  json_response([
    'ok' => true,
    'terminated_jobs' => $terminated,
    'pending_jobs' => count_pending_jobs($conversationId),
  ]);
}

if ($action === 'message_feedback') {
  $conversationId = (int) ($input['conversation_id'] ?? 0);
  $messageId = (int) ($input['message_id'] ?? 0);
  $reaction = strtolower(trim((string) ($input['reaction'] ?? '')));

  if ($conversationId <= 0 || $messageId <= 0) {
    json_response(['error' => 'bad_request'], 400);
  }

  require_conversation_ownership($conversationId, $userId);

  $stmt = db()->prepare('SELECT id, role FROM messages WHERE id = ? AND conversation_id = ? LIMIT 1');
  $stmt->execute([$messageId, $conversationId]);
  $messageRow = $stmt->fetch();
  if (!$messageRow || ($messageRow['role'] ?? '') !== 'assistant') {
    json_response(['error' => 'not_found'], 404);
  }

  if ($reaction === 'clear') {
    clear_message_feedback($messageId, $conversationId, $userId);
  } elseif (in_array($reaction, ['up', 'down'], true)) {
    upsert_message_feedback($messageId, $conversationId, $userId, $reaction);
  } else {
    json_response(['error' => 'bad_request'], 400);
  }

  json_response(['ok' => true, 'reaction' => $reaction]);
}

if ($action === 'mcp_servers') {
  $conversationId = (int) ($input['conversation_id'] ?? 0);
  if ($conversationId > 0) {
    require_conversation_ownership($conversationId, $userId);
  }

  $servers = list_mcp_servers();
  $allowed = $conversationId > 0 ? get_active_mcp_override_servers($conversationId) : null;
  $overrideActive = is_array($allowed);

  if ($overrideActive) {
    $allowedLookup = array_flip($allowed);
    foreach ($servers as &$server) {
      $serverName = strtolower(trim((string)($server['name'] ?? '')));
      $server['effective_is_active'] = isset($allowedLookup[$serverName]);
      $server['forced_by_schedule'] = true;
    }
    unset($server);
  } else {
    foreach ($servers as &$server) {
      $server['effective_is_active'] = (bool)($server['is_active'] ?? false);
      $server['forced_by_schedule'] = false;
    }
    unset($server);
  }

  json_response([
    'servers' => $servers,
    'override_active' => $overrideActive,
    'override_allowed' => $allowed ?? [],
  ]);
}

if ($action === 'toggle_mcp_server') {
  $id = (int) ($input['id'] ?? 0);
  $active = (bool) ($input['is_active'] ?? false);
  if ($id <= 0) {
    json_response(['error' => 'bad_request'], 400);
  }
  toggle_mcp_server($id, $active);
  json_response(['ok' => true]);
}

if ($action === 'get_profile') {
  json_response(['profile' => get_user_profile($userId)]);
}

if ($action === 'update_profile') {
  $persona = trim((string) ($input['persona'] ?? ''));
  $blueprints = trim((string) ($input['blueprints'] ?? ''));
  update_user_profile($userId, $persona, $blueprints);
  json_response(['ok' => true]);
}

if ($action === 'delete_conversation') {
  $conversationId = (int) ($input['conversation_id'] ?? 0);
  if ($conversationId <= 0) {
    json_response(['error' => 'bad_request'], 400);
  }
  require_conversation_ownership($conversationId, $userId);
  delete_user_conversation($conversationId);
  json_response(['ok' => true]);
}

if ($action === 'get_pending_approvals') {
  $conversationId = (int) ($input['conversation_id'] ?? 0);
  if ($conversationId > 0) {
    require_conversation_ownership($conversationId, $userId);
  }
  json_response(['approvals' => get_pending_tool_approvals($conversationId)]);
}

if ($action === 'get_pending_clarifications') {
  $conversationId = (int) ($input['conversation_id'] ?? 0);
  if ($conversationId > 0) {
    require_conversation_ownership($conversationId, $userId);
  }
  json_response(['clarifications' => get_pending_clarification_requests($conversationId)]);
}

if ($action === 'approve_tool') {
  $approvalId = (int) ($input['approval_id'] ?? 0);
  if ($approvalId <= 0) {
    json_response(['error' => 'bad_request'], 400);
  }
  resolve_tool_approval($approvalId, $userId, 'approved');
  json_response(['ok' => true]);
}

if ($action === 'deny_tool') {
  $approvalId = (int) ($input['approval_id'] ?? 0);
  if ($approvalId <= 0) {
    json_response(['error' => 'bad_request'], 400);
  }
  resolve_tool_approval($approvalId, $userId, 'denied');
  json_response(['ok' => true]);
}

if ($action === 'answer_clarification') {
  $requestId = (int) ($input['request_id'] ?? 0);
  $answerText = trim((string) ($input['answer'] ?? ''));
  if ($requestId <= 0 || $answerText === '') {
    json_response(['error' => 'bad_request'], 400);
  }

  $request = get_clarification_request_by_id($requestId, $userId);
  if (!$request || (int)($request['conversation_id'] ?? 0) <= 0) {
    json_response(['error' => 'not_found'], 404);
  }

  resolve_clarification_request($requestId, $userId, $answerText);
  json_response(['ok' => true]);
}

json_response(['error' => 'unknown_action'], 404);

