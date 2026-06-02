<?php
require __DIR__ . '/../app/bootstrap.php';

if (current_user()) {
  redirect_to('/');
}

$success = '';
if (isset($_GET['success']) && $_GET['success'] === '1') {
  $success = 'Account succesvol aangevraagd! Wacht op goedkeuring van de beheerder.';
}

$error = '';
if ($_SERVER['REQUEST_METHOD'] === 'POST') {
  $username = trim($_POST['username'] ?? '');
  $password = $_POST['password'] ?? '';
  $user = find_user_by_username($username);

  if ($user) {
    if ($user['status'] !== 'approved') {
      $error = 'Je account is nog afwachting van goedkeuring of is afgewezen.';
    } else {
      if (password_verify($password, $user['password_hash'])) {
        login($user);
        redirect_to('/');
      } elseif ($user['password_hash'] === $password && !str_starts_with($user['password_hash'], '$')) {
        // Fallback to upgrade plaintext passwords (e.g. from schema.sql seeding)
        $hash = password_hash($password, PASSWORD_DEFAULT);
        $stmt = db()->prepare('UPDATE users SET password_hash = ? WHERE id = ?');
        $stmt->execute([$hash, $user['id']]);
        login($user);
        redirect_to('/');
      } else {
        $error = 'Invalid credentials';
      }
    }
  } else {
    $error = 'Invalid credentials';
  }
}

$title = 'Login';
$stylesheet = '/assets/css/app.css';

require __DIR__ . '/../app/views/head.php';
require __DIR__ . '/../app/views/login_form.php';
