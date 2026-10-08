-- Descuentos directos vigentes (precio actual menor al regular), del más grande al más chico.
SELECT sm.code AS supermarket,
       l.name AS producto,
       lp.regular_price AS precio_regular,
       lp.price AS precio_actual,
       ROUND(100.0 * (lp.regular_price - lp.price) / lp.regular_price, 1) AS descuento_pct,
       lp.promo_text AS otras_promos
FROM v_latest_prices lp
JOIN listings l ON l.id = lp.listing_id
JOIN supermarkets sm ON sm.id = lp.supermarket_id
WHERE lp.is_available AND lp.regular_price > lp.price
ORDER BY descuento_pct DESC, l.name
LIMIT 20;
