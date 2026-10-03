
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
SELECT posting.id::text AS posting_id,
       board.id::text AS board_id,
       posting.source_url
FROM contract
JOIN job_board AS board
  ON board.board_slug = contract.board_slug
JOIN job_posting AS posting
  ON posting.board_id = board.id
ORDER BY posting.id
