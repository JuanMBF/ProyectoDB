-- Canasta comparable: productos (mismo código de barras) que hoy tienen precio en TODOS los
-- supermercados relevados, y cuánto cuesta esa misma canasta en cada uno.
WITH cadenas AS (
    SELECT COUNT(DISTINCT supermarket) AS total
    FROM v_price_comparison
),
canasta AS (
    SELECT product_id
    FROM v_price_comparison
    GROUP BY product_id
    HAVING COUNT(*) = (SELECT total FROM cadenas)
)
SELECT c.supermarket,
       COUNT(*) AS productos,
       SUM(c.price) AS total_canasta,
       SUM(c.regular_price) AS total_sin_descuentos,
       SUM(CASE WHEN c.price_rank = 1 THEN 1 ELSE 0 END) AS productos_mas_baratos
FROM v_price_comparison c
JOIN canasta k ON k.product_id = c.product_id
GROUP BY c.supermarket
ORDER BY total_canasta;
