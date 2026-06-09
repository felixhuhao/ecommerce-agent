# ecommerce_analysis helper API

Use these pre-baked helpers for commerce time-series analysis instead of writing pandas from
scratch. They read files the agent already wrote into `/workspace`; they never fetch data and never
touch the network.

- `load_orders_df(path, products_path=None) -> DataFrame`
  Parse either flat line-item JSON/CSV records or raw Spring `order_query` JSON. When
  `products_path` is provided, raw Spring `product_query` JSON is used to map product IDs to
  categories; missing product IDs become `unknown`.
- `monthly_sales_by_category(orders_df) -> DataFrame`
  Realized sales only, where status is `paid`, `shipped`, or `completed`. Returns `month`,
  `category`, `sales`.
- `simple_forecast(monthly_df, periods=1) -> DataFrame`
  Per-category linear-trend forecast. Returns `category`, `month`, `sales`, `is_forecast`.
- `validate_forecast_result(forecast_df) -> None`
  Raises `ValueError` if the forecast is empty, non-finite, or has no forecast rows.
