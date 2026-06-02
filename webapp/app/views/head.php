<?php
/** @var string $title */
/** @var string $stylesheet */

$stylesheetHref = $stylesheet;
$stylePath = parse_url($stylesheet, PHP_URL_PATH);
if (is_string($stylePath) && $stylePath !== '') {
  $fullStylePath = rtrim((string) ($_SERVER['DOCUMENT_ROOT'] ?? ''), '/') . $stylePath;
  if (is_file($fullStylePath)) {
    $separator = str_contains($stylesheet, '?') ? '&' : '?';
    $stylesheetHref = $stylesheet . $separator . 'v=' . filemtime($fullStylePath);
  }
}
?>
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title><?= htmlspecialchars($title) ?></title>
  <link rel="stylesheet" href="<?= htmlspecialchars($stylesheetHref) ?>">
  <!-- Markdown & Highlighting -->
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github-dark.min.css">
  <script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"></script>
  <script src="https://cdnjs.cloudflare.com/ajax/libs/marked/12.0.1/marked.min.js"></script>
  <script src="https://cdnjs.cloudflare.com/ajax/libs/dompurify/3.0.9/purify.min.js"></script>
  <!-- Tailwind CSS via CDN (with typography for markdown parsing) -->
  <script src="https://cdn.tailwindcss.com?plugins=typography"></script>
  <!-- Phosphor Icons via CDN (for sleek icons) -->
  <script src="https://unpkg.com/@phosphor-icons/web"></script>
  
  <script>
    tailwind.config = {
      darkMode: 'class',
      theme: {
        extend: {
          colors: {
            background: '#0d0d0d',
            panel: '#151515',
            surface: '#1e1e1e',
            border: '#2a2a2a',
            muted: '#a1a1a1',
            text: '#f3f3f3',
            primary: {
              DEFAULT: '#3b82f6',
              foreground: '#ffffff',
            }
          },
          fontFamily: {
            sans: ['Inter', 'ui-sans-serif', 'system-ui', '-apple-system', 'Segoe UI', 'Roboto', 'sans-serif'],
          }
        }
      }
    }
  </script>
  <style>
    .sidebar-hidden #sidebar {
      width: 0 !important;
      border: none !important;
      opacity: 0;
      pointer-events: none;
    }
  </style>
</head>
