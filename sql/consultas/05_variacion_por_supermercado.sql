-- Variación de precios por supermercado entre la primera y la última captura de cada producto
-- (FIRST_VALUE / LAST_VALUE sobre una ventana con nombre).
WITH extremos AS (
    SELECT DISTINCT s.listing_id,
           FIRST_VALUE(s.price) OVER w AS primer_precio,
           LAST_VALUE(s.price) OVER w AS ultimo_precio,
           COUNT(*) OVER w AS capturas
    FROM price_snapshots s
    WHERE s.price IS NOT NULL
    WINDOW w AS (
        PARTITION BY s.listing_id ORDER BY s.captured_at, s.id
        ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
    )
)
SELECT sm.code AS supermarket,
       COUNT(*) AS productos_seguidos,
       ROUND(AVG(100.0 * (e.ultimo_precio - e.primer_precio) / e.primer_precio), 2) AS variacion_promedio_pct,
       SUM(CASE WHEN e.ultimo_precio > e.primer_precio THEN 1 ELSE 0 END) AS subieron,
       SUM(CASE WHEN e.ultimo_precio < e.primer_precio THEN 1 ELSE 0 END) AS bajaron
FROM extremos e
JOIN listings l ON l.id = e.listing_id
JOIN supermarkets sm ON sm.id = l.supermarket_id
WHERE e.capturas >= 2
GROUP BY sm.code
ORDER BY variacion_promedio_pct DESC;
