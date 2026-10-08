-- En cuántos productos cada supermercado tiene el precio más bajo (los empates cuentan para todos)
-- y cuánto más caro es, en promedio, respecto del mejor precio.
SELECT supermarket,
       COUNT(*) AS productos_comparados,
       SUM(CASE WHEN price_rank = 1 THEN 1 ELSE 0 END) AS veces_mas_barato,
       ROUND(AVG(pct_vs_best), 1) AS sobreprecio_promedio_pct
FROM v_price_comparison
GROUP BY supermarket
ORDER BY veces_mas_barato DESC, sobreprecio_promedio_pct;
