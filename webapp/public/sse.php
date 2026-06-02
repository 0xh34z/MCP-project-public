<?php
require __DIR__ . '/../app/bootstrap.php';

require_auth();

$user = current_user();
$userId = (int) $user['id'];
$conversationId = (int) ($_GET['conversation_id'] ?? 0);

if ($conversationId <= 0) {
    http_response_code(400);
    exit;
}

require_conversation_ownership($conversationId, $userId);

header('Content-Type: text/event-stream');
header('Cache-Control: no-cache');
header('Connection: keep-alive');
header('X-Accel-Buffering: no');

@ini_set('zlib.output_compression', '0');
@ini_set('implicit_flush', '1');
while (ob_get_level() > 0) {
    ob_end_flush();
}
ob_implicit_flush(true);

function get_conversation_state_summary(int $conversationId): array {
    $stmt = db()->prepare("SELECT 
      (SELECT COUNT(*) FROM messages WHERE conversation_id = :cid) as msg_count,
      (SELECT MAX(id) FROM messages WHERE conversation_id = :cid) as max_msg_id,
    (SELECT MAX(updated_at) FROM messages WHERE conversation_id = :cid) as last_message_updated_at,
      (SELECT COUNT(*) FROM jobs WHERE conversation_id = :cid AND status IN ('pending', 'running')) as pending_jobs,
      (SELECT UNIX_TIMESTAMP(MIN(scheduled_at)) FROM jobs WHERE conversation_id = :cid AND status IN ('pending', 'running')) as next_scheduled_at,
      (SELECT COUNT(*) FROM tool_approvals WHERE conversation_id = :cid AND status = 'pending') as pending_approvals,
      (SELECT MAX(id) FROM tool_approvals WHERE conversation_id = :cid AND status = 'pending') as max_approval_id,
      (SELECT COUNT(*) FROM clarification_requests WHERE conversation_id = :cid AND status = 'pending') as pending_clarifications,
      (SELECT MAX(id) FROM clarification_requests WHERE conversation_id = :cid AND status = 'pending') as max_clarification_id,
      (SELECT 1 FROM jobs WHERE conversation_id = :cid AND status IN ('pending', 'running') AND COALESCE(auto_approve_tools, 0) = 1 AND (status = 'running' OR scheduled_at <= NOW()) LIMIT 1) as auto_approve_active,
      (SELECT mcp_servers FROM jobs WHERE conversation_id = :cid AND status IN ('pending', 'running') AND mcp_servers IS NOT NULL AND TRIM(mcp_servers) <> '' AND (status = 'running' OR scheduled_at <= NOW()) ORDER BY (status = 'running') DESC, id ASC LIMIT 1) as mcp_servers");
    $stmt->execute([':cid' => $conversationId]);
    return $stmt->fetch(PDO::FETCH_ASSOC) ?: [];
}

$initialState = get_conversation_state_summary($conversationId);
$initialApprovals = get_pending_tool_approvals($conversationId);
$initialClarifications = get_pending_clarification_requests($conversationId);
$initialLlmSelection = get_conversation_llm_selection($conversationId);
$initialMcpOverrideHash = get_active_mcp_override_hash($conversationId);

// Send initial state so the client knows we've connected
echo "data: " . json_encode([
    'connected' => true,
    'pending_jobs' => (int) $initialState['pending_jobs'],
    'next_scheduled_at' => $initialState['next_scheduled_at'],
    'pending_approvals' => $initialApprovals,
    'pending_clarifications' => $initialClarifications,
    'auto_approve_active' => (bool) $initialState['auto_approve_active'],
    'mcp_override_hash' => $initialMcpOverrideHash,
    'llm_selection' => $initialLlmSelection
]) . "\n\n";
flush();

$startTime = time();
$maxExecutionTime = 300; // Close connection after 5 minutes and let client reconnect
$lastPingTime = time();

session_write_close(); // Prevent locking the session file

$lastState = $initialState;

while (true) {
    @set_time_limit(0);

    if (connection_aborted() || (time() - $startTime) > $maxExecutionTime) {
        break;
    }

    $currentState = get_conversation_state_summary($conversationId);

    if ($currentState !== $lastState) {
        $currentMessages = list_conversation_messages($conversationId, $userId);
        $currentPendingJobs = (int) $currentState['pending_jobs'];
        $currentNextScheduledAt = $currentState['next_scheduled_at'];
        $currentAutoApproveActive = (bool) $currentState['auto_approve_active'];
        $currentMcpOverrideHash = get_active_mcp_override_hash($conversationId);
        $currentLlmSelection = get_conversation_llm_selection($conversationId);
        $currentApprovals = get_pending_tool_approvals($conversationId);
        $currentClarifications = get_pending_clarification_requests($conversationId);

        $data = [
            'messages' => $currentMessages,
            'pending_jobs' => $currentPendingJobs,
            'next_scheduled_at' => $currentNextScheduledAt,
            'pending_approvals' => $currentApprovals,
            'pending_clarifications' => $currentClarifications,
            'auto_approve_active' => $currentAutoApproveActive,
            'mcp_override_hash' => $currentMcpOverrideHash,
            'llm_selection' => $currentLlmSelection,
        ];
        
        echo "data: " . json_encode($data) . "\n\n";
        flush();

        $lastState = $currentState;
    }

    // Send SSE ping every 15 seconds to keep connection alive
    if (time() - $lastPingTime >= 15) {
        echo ": ping\n\n";
        flush();
        $lastPingTime = time();
    }
    
    // Adaptive interval: refresh faster while jobs are running for near-real-time streaming,
    // but back off when idle to reduce DB load.
    usleep(((int)$currentState['pending_jobs']) > 0 ? 200000 : 500000);
}
