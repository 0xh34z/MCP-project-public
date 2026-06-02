CREATE TABLE IF NOT EXISTS users (
  id INT AUTO_INCREMENT PRIMARY KEY,
  username VARCHAR(64) UNIQUE NOT NULL,
  password_hash VARCHAR(255) NOT NULL,
  api_key VARCHAR(128) UNIQUE NULL,
  role ENUM('admin', 'user') DEFAULT 'user',
  status ENUM('pending', 'approved', 'rejected') DEFAULT 'pending',
  persona MEDIUMTEXT NULL,
  blueprints MEDIUMTEXT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Seed initial admin user using the credentials from the shell script.
-- The application login.php logic automatically hashes this plaintext password upon first successful login.
INSERT INTO users (username, password_hash, role, status) VALUES ('user', 'uCrhlQyvXpkeShPwOSQxaMQxZ', 'admin', 'approved') ON DUPLICATE KEY UPDATE id=id;

CREATE TABLE IF NOT EXISTS conversations (
  id INT AUTO_INCREMENT PRIMARY KEY,
  user_id INT NOT NULL,
  title VARCHAR(255) NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS llm_providers (
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
);

CREATE TABLE IF NOT EXISTS messages (
  id INT AUTO_INCREMENT PRIMARY KEY,
  conversation_id INT NOT NULL,
  role VARCHAR(20) NOT NULL,
  content MEDIUMTEXT NOT NULL,
  llm_provider_id INT NULL,
  llm_model VARCHAR(255) NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX idx_messages_conversation_id_id (conversation_id, id),
  INDEX idx_messages_conversation_id_updated_at (conversation_id, updated_at),
  FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
  FOREIGN KEY (llm_provider_id) REFERENCES llm_providers(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS jobs (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  conversation_id INT NOT NULL,
  user_id INT NOT NULL,
  prompt MEDIUMTEXT NOT NULL,
  status ENUM('pending','running','done','error') DEFAULT 'pending',
  result_text MEDIUMTEXT NULL,
  error_text TEXT NULL,
  scheduled_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  repeat_count INT DEFAULT 1,
  repeat_interval INT DEFAULT 0, -- in minutes
  llm_provider_id INT NULL,
  llm_model VARCHAR(255) NULL,
  llm_api_url VARCHAR(255) NULL,
  mcp_servers TEXT NULL,
  auto_approve_tools TINYINT(1) NULL DEFAULT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX(status),
  INDEX(scheduled_at),
  INDEX idx_jobs_conversation_status (conversation_id, status, scheduled_at, id),
  INDEX idx_jobs_llm_provider_id (llm_provider_id),
  FOREIGN KEY (llm_provider_id) REFERENCES llm_providers(id) ON DELETE SET NULL,
  FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS chat_message_feedback (
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
);

CREATE TABLE IF NOT EXISTS tool_usage_logs (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  job_id BIGINT NOT NULL,
  conversation_id INT NOT NULL,
  user_id INT NOT NULL,
  tool_name VARCHAR(128) NOT NULL,
  server_name VARCHAR(128) NULL,
  arguments_json MEDIUMTEXT NULL,
  status ENUM('running', 'completed', 'error', 'unavailable') NOT NULL DEFAULT 'running',
  success TINYINT(1) NOT NULL DEFAULT 0,
  duration_ms INT NULL,
  output_text MEDIUMTEXT NULL,
  error_text TEXT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX idx_tool_usage_created_at (created_at),
  INDEX idx_tool_usage_tool_name (tool_name),
  INDEX idx_tool_usage_status (status),
  INDEX idx_tool_usage_job_id (job_id),
  FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE,
  FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS settings (
  `key` VARCHAR(64) PRIMARY KEY,
  `value` TEXT NOT NULL,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

INSERT INTO settings (`key`, `value`) VALUES
  ('llm_api_url',   'http://192.168.1.196:8080'),
  ('llm_model',     'deepseek/deepseek-v4-flash'),
  ('default_llm_provider_id', '0'),
  ('llm_timeout',   '300'),
  ('max_tool_calls','-1'),
  ('tool_approval_timeout', '900'),
  ('poll_interval', '0.5'),
  ('context_window_messages', '8'),
  ('context_summary_enabled', '1'),
  ('context_summary_max_chars', '800'),
  ('context_max_message_chars', '1200'),
  ('context_include_tool_traces', '0'),
  ('tool_permission_required', '1'),
  ('context_turbo_limit', '-1'),
  ('llm_router_enabled', '1'),
  ('llm_router_trigger_name', 'router'),
  ('llm_router_low_provider_id', '0'),
  ('llm_router_low_model', 'deepseek/deepseek-v4-flash'),
  ('llm_router_high_provider_id', '0'),
  ('llm_router_high_model', 'deepseek/deepseek-v4-flash')
ON DUPLICATE KEY UPDATE `value`=VALUES(`value`);

CREATE TABLE IF NOT EXISTS mcp_servers (
  id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(64) NOT NULL,
  type ENUM('stdio', 'streamable-http', 'sse') NOT NULL,
  command TEXT NULL,
  url TEXT NULL,
  is_active BOOLEAN DEFAULT TRUE,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS job_mcp_servers (
  job_id BIGINT NOT NULL,
  mcp_server_id INT NOT NULL,
  PRIMARY KEY (job_id, mcp_server_id),
  FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE,
  FOREIGN KEY (mcp_server_id) REFERENCES mcp_servers(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS tool_approvals (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  job_id BIGINT NOT NULL,
  conversation_id INT NOT NULL,
  user_id INT NOT NULL,
  tool_name VARCHAR(128) NOT NULL,
  server_name VARCHAR(128) NULL,
  arguments_json MEDIUMTEXT NULL,
  status ENUM('pending','approved','denied') NOT NULL DEFAULT 'pending',
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX idx_tool_approvals_job_id (job_id),
  INDEX idx_tool_approvals_status (status),
  INDEX idx_tool_approvals_conversation_status (conversation_id, status, id),
  FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE,
  FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS clarification_requests (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  job_id BIGINT NOT NULL,
  conversation_id INT NOT NULL,
  user_id INT NOT NULL,
  question MEDIUMTEXT NOT NULL,
  details_json MEDIUMTEXT NULL,
  answer_text MEDIUMTEXT NULL,
  status ENUM('pending','answered','closed') NOT NULL DEFAULT 'pending',
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX idx_clarification_requests_job_id (job_id),
  INDEX idx_clarification_requests_status (status),
  INDEX idx_clarification_requests_conversation_status (conversation_id, status, id),
  FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE,
  FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

INSERT INTO mcp_servers (name, type, url) VALUES 
  ('Proxmox MCP', 'streamable-http', 'http://192.168.1.100:5002/mcp'),
  ('Kali MCP', 'streamable-http', 'http://192.168.1.101:5001/mcp')
ON DUPLICATE KEY UPDATE id=id;

INSERT INTO llm_providers (name, provider_type, base_url, api_key, default_model, is_active) VALUES
  ('Llama.cpp Router', 'openai-compatible', 'http://192.168.1.196:8080', '', 'Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive-Q4_K_M', TRUE)
ON DUPLICATE KEY UPDATE `api_key`=VALUES(`api_key`), `default_model`=VALUES(`default_model`), `is_active`=VALUES(`is_active`);

-- Seed OpenRouter provider as the default LLM provider for clean installs
INSERT INTO llm_providers (name, provider_type, base_url, api_key, default_model, is_active) VALUES
  ('OpenRouter', 'openai-compatible', 'https://openrouter.ai/api/v1', '__OPENROUTER_API_KEY__', 'deepseek/deepseek-v4-flash', TRUE)
ON DUPLICATE KEY UPDATE `api_key`=VALUES(`api_key`), `default_model`=VALUES(`default_model`), `is_active`=VALUES(`is_active`);

-- Resolve the Llama.cpp Router id into a variable and use it for settings.
SET @llama_id = (SELECT id FROM llm_providers WHERE name = 'Llama.cpp Router' LIMIT 1);

-- Resolve the OpenRouter id into a variable for settings.
SET @openrouter_id = (SELECT id FROM llm_providers WHERE name = 'OpenRouter' LIMIT 1);

INSERT INTO settings (`key`, `value`) VALUES ('default_llm_provider_id', IFNULL(@llama_id, '0'))
ON DUPLICATE KEY UPDATE `value`=VALUES(`value`);

INSERT INTO settings (`key`, `value`) VALUES ('llm_model', 'deepseek/deepseek-v4-flash')
ON DUPLICATE KEY UPDATE `value`=VALUES(`value`);

INSERT INTO settings (`key`, `value`) VALUES ('default_llm_provider_id', IFNULL(@openrouter_id, '0'))
ON DUPLICATE KEY UPDATE `value`=VALUES(`value`);

INSERT INTO settings (`key`, `value`) VALUES ('llm_router_low_provider_id', IFNULL(@openrouter_id, '0'))
ON DUPLICATE KEY UPDATE `value`=VALUES(`value`);

INSERT INTO settings (`key`, `value`) VALUES ('llm_router_high_provider_id', IFNULL(@openrouter_id, '0'))
ON DUPLICATE KEY UPDATE `value`=VALUES(`value`);

INSERT INTO settings (`key`, `value`) VALUES ('llm_router_low_model', 'deepseek/deepseek-v4-flash')
ON DUPLICATE KEY UPDATE `value`=VALUES(`value`);

INSERT INTO settings (`key`, `value`) VALUES ('llm_router_high_model', 'deepseek/deepseek-v4-flash')
ON DUPLICATE KEY UPDATE `value`=VALUES(`value`);
