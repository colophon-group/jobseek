
        UPDATE job_posting
        SET is_active = false,
            next_scrape_at = NULL,
            updated_at = now()
        WHERE id IN (
            SELECT jp.id
            FROM job_posting jp
            JOIN job_board jb ON jb.id = jp.board_id
            WHERE jp.is_active = true
              AND jb.is_enabled = false
              AND jb.board_status IN ('disabled', 'gone')
            LIMIT $1
            FOR UPDATE OF jp SKIP LOCKED
        )
    