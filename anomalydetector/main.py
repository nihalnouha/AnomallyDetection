import os
from collections import defaultdict

import numpy as np
# Load environment variables from a .env file for local development
from dotenv import load_dotenv
from quixstreams import Application
from sklearn.ensemble import IsolationForest

# Load environment variables
load_dotenv()

# Initialize the Kafka application with a consumer group and configuration
app = Application(consumer_group="transformation-v1",
                  auto_offset_reset="earliest",
                  broker_address='kafka_broker:9092')

# Configure the input and output Kafka topics
input_topic = app.topic(os.environ["input"])
output_topic = app.topic(os.environ["output"])

# Default threshold for detecting high-volume trades, per symbol
high_volume_threshold = defaultdict(lambda: 20000)

# List to store prices for training the Isolation Forest model
fit_prices = []
is_fitted = False  # Indicates whether the Isolation Forest model has been trained

# Initialize the Isolation Forest algorithm with specific parameters
isolation_forest = IsolationForest(contamination=0.01, n_estimators=1000)

# Function to detect anomalies based on high trade volume
def high_volume_rule(trade_data):
    # Mark an anomaly if trade size exceeds the threshold for the given symbol
    trade_data['high_volume_anomaly'] = bool(trade_data['size'] > high_volume_threshold[trade_data['symbol']])
    return trade_data

# Function to detect anomalies using the Isolation Forest algorithm
def isolation_forest_rule(trade_data):
    global is_fitted
    current_price = trade_data['price']

    # Add the current price to the list of collected prices
    fit_prices.append(float(current_price))

    # Wait until at least 1000 prices are collected before training the model
    if len(fit_prices) < 1000:
        trade_data['isolation_forest_anomaly'] = False
        return trade_data

    # Normalize the collected prices
    fit_prices_normalised = (np.array(fit_prices) - np.mean(fit_prices)) / np.std(fit_prices)
    prices_reshaped = fit_prices_normalised.reshape(-1, 1)

    # Train the model every 1000 new prices
    if len(fit_prices) % 1000 == 0:
        isolation_forest.fit(prices_reshaped)
        is_fitted = True

    # If the model is not yet trained, do not detect anomalies
    if not is_fitted:
        trade_data['isolation_forest_anomaly'] = False
        return trade_data

    # Normalize the current price for anomaly detection
    current_price_normalised = (current_price - float(np.mean(fit_prices))) / float(np.std(fit_prices))
    score = isolation_forest.decision_function([[current_price_normalised]])

    # Detect an anomaly if the score is negative
    trade_data['isolation_forest_anomaly'] = bool(score[0] < 0)

    return trade_data

# Function to combine detected anomalies from different rules
def combine_anomalies(trade_data):
    anomalies = []

    # Check and add each type of detected anomaly
    if trade_data.get('high_volume_anomaly'):
        anomalies.append('High Volume')
    if trade_data.get('isolation_forest_anomaly'):
        anomalies.append('Isolation Forest Anomaly')

    # Add the list of detected anomalies or None if no anomalies are found
    trade_data['anomalies'] = anomalies if anomalies else None

    return trade_data

if __name__ == "__main__":
    # Create a dataframe based on the input Kafka topic
    sdf = app.dataframe(input_topic)

    # Apply rules and transformations to the dataframe
    sdf = (sdf
           .apply(high_volume_rule)  # Apply the high-volume rule
           .apply(isolation_forest_rule)  # Apply the Isolation Forest rule
           .apply(combine_anomalies)  # Combine detected anomalies
           )

    # Filter rows that have at least one detected anomaly
    sdf = sdf.filter(lambda row: row.get('anomalies') and len(row['anomalies']) >= 1)

    # Publish the filtered data to the output Kafka topic
    sdf.to_topic(output_topic)

    # Potential integrations (not implemented here)
    # elasticsearch
    # postgres
    # streamlit

    # Run the Kafka application
    app.run(sdf)
