
WITH contract (board_slug) AS (
    VALUES
('bobst-global'),
('bucherer-careers-umantis'),
('canton-neuchatel-careers'),
('fhgr-careers'),
('j-safra-sarasin-careers'),
('lindt-spruengli-careers'),
('ruag-main')
)
SELECT board.id::text AS board_id,
       board.throttle_key
FROM contract
JOIN job_board AS board
  ON board.board_slug = contract.board_slug
ORDER BY board.id
