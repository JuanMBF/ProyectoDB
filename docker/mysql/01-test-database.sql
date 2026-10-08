-- Base aparte para los tests de integración, así nunca tocan los datos de `precios`.
-- MySQL ejecuta este archivo solo la primera vez que inicializa el volumen.
CREATE DATABASE IF NOT EXISTS precios_test CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
GRANT ALL PRIVILEGES ON precios_test.* TO 'precios'@'%';
