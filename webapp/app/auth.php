<?php
session_name('gui_session');
session_start();

function current_user() {
  return $_SESSION['user'] ?? null;
}

function require_auth() {
  if (!current_user()) {
    header('Location: /login.php');
    exit;
  }
}

function require_admin() {
  require_auth();
  $u = current_user();
  if (($u['role'] ?? 'user') !== 'admin') {
    http_response_code(403);
    echo "<h1>403 Forbidden</h1><p>Je hebt admin-rechten nodig om deze pagina te bekijken.</p><a href=\"/\">Ga terug</a>";
    exit;
  }
}

function login($user) {
  session_regenerate_id(true);
  $_SESSION['user'] = ['id' => (int)$user['id'], 'username' => $user['username'], 'role' => $user['role'] ?? 'user'];
}

function logout_user() {
  $_SESSION = [];
  if (ini_get('session.use_cookies')) {
    $params = session_get_cookie_params();
    setcookie(session_name(), '', time() - 42000, $params['path'], $params['domain'], $params['secure'], $params['httponly']);
  }
  session_destroy();
}
