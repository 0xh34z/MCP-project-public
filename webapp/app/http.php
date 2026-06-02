<?php
function redirect_to(string $path): void {
  header('Location: ' . $path);
  exit;
}

function json_input(): array {
  $raw = file_get_contents('php://input');
  if (!$raw) {
    return [];
  }

  $decoded = json_decode($raw, true);
  return is_array($decoded) ? $decoded : [];
}

function json_response(array $payload, int $status = 200): void {
  http_response_code($status);
  header('Content-Type: application/json');
  echo json_encode($payload);
  exit;
}
