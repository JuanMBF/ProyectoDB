-- Productos con mayor diferencia de precio entre el supermercado más caro y el más barato.
SELECT ean,
       product AS producto,
       COUNT(*) AS cadenas,
       MIN(price) AS precio_minimo,
       MAX(price) AS precio_maximo,
       MAX(price) - MIN(price) AS brecha,
       ROUND(100.0 * (MAX(price) - MIN(price)) / MIN(price), 1) AS brecha_pct
FROM v_price_comparison
GROUP BY ean, product
ORDER BY brecha_pct DESC, ean
LIMIT 10;
