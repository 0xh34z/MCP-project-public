<?php
require __DIR__ . '/../app/bootstrap.php';

if (current_user()) {
  redirect_to('/');
}

$error = '';
if ($_SERVER['REQUEST_METHOD'] === 'POST') {
  $username = trim($_POST['username'] ?? '');
  $password = $_POST['password'] ?? '';
  $password_confirm = $_POST['password_confirm'] ?? '';
  $gdpr_consent = isset($_POST['gdpr_consent']) ? true : false;

  if (empty($username) || empty($password)) {
    $error = 'Please fill in all fields.';
  } elseif (!$gdpr_consent) {
    $error = 'You must agree to the data processing policy (GDPR/AVG) to register.';
  } elseif ($password !== $password_confirm) {
    $error = 'Passwords do not match.';
  } else {
    $existing = find_user_by_username($username);
    if ($existing) {
      $error = 'Username is already taken.';
    } else {
      $hash = password_hash($password, PASSWORD_DEFAULT);
      $stmt = db()->prepare("INSERT INTO users (username, password_hash, role, status) VALUES (?, ?, 'user', 'pending')");
      if ($stmt->execute([$username, $hash])) {
        redirect_to('/login.php?success=1');
      } else {
        $error = 'Registration failed.';
      }
    }
  }
}

$title = 'Register';
$stylesheet = '/assets/css/app.css';

require __DIR__ . '/../app/views/head.php';
require __DIR__ . '/../app/views/register_form.php';
