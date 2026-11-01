-- ===== SCHEMA =====
CREATE TABLE IF NOT EXISTS countries (
    country_id    INT UNSIGNED NOT NULL AUTO_INCREMENT,
    country_name  VARCHAR(64)  NOT NULL,
    is_banned     BOOLEAN      NOT NULL DEFAULT FALSE,
    PRIMARY KEY (country_id),
    UNIQUE (country_name)
);

CREATE TABLE IF NOT EXISTS client_ips (
    client_ip   VARCHAR(45)  NOT NULL,
    country_id  INT UNSIGNED NULL,
    PRIMARY KEY (client_ip),
    FOREIGN KEY (country_id) REFERENCES countries (country_id)
);

CREATE TABLE IF NOT EXISTS requests_3nf (
    request_id      BIGINT UNSIGNED NOT NULL,
    client_ip       VARCHAR(45)  NULL,
    gender          VARCHAR(32)  NULL,
    age             VARCHAR(32)  NULL,
    income          VARCHAR(32)  NULL,
    time_of_day     DATETIME     NOT NULL,
    requested_file  VARCHAR(255) NULL,
    PRIMARY KEY (request_id),
    FOREIGN KEY (client_ip) REFERENCES client_ips (client_ip)
);

-- ===== MIGRATE =====
INSERT INTO countries (country_name, is_banned)
SELECT country, MAX(is_banned)
FROM requests
WHERE country IS NOT NULL
GROUP BY country;

INSERT INTO client_ips (client_ip, country_id)
SELECT r.client_ip, MIN(c.country_id)
FROM requests r
LEFT JOIN countries c ON c.country_name = r.country
WHERE r.client_ip IS NOT NULL
GROUP BY r.client_ip;

INSERT INTO requests_3nf (request_id, client_ip, gender, age, income, time_of_day, requested_file)
SELECT request_id, client_ip, gender, age, income, time_of_day, requested_file
FROM requests;

-- ===== CHECKS =====
SELECT COUNT(*) FROM (SELECT client_ip FROM requests GROUP BY client_ip HAVING COUNT(DISTINCT country) > 1) t;
SELECT COUNT(*) FROM requests o
JOIN requests_3nf n ON n.request_id = o.request_id
LEFT JOIN client_ips i ON i.client_ip = n.client_ip
LEFT JOIN countries c ON c.country_id = i.country_id
WHERE c.country_name <=> o.country AND COALESCE(c.is_banned, FALSE) = o.is_banned;
