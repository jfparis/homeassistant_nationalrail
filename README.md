# Home Assistant National Rail API Integration

This is an integration for the national rail API into home assistant.
This allows you to have a schedule of the train at your station in your home assistant.

# Instalation

1. Register with national rail to get an api token [here](http://realtime.nationalrail.co.uk/OpenLDBWSRegistration/)
2. Copy the custom_components/nationalrailuk folder into your config/custom_components folder and restart home assistant
3. Find the crs code for your origin and destination station using the [National Rail website](https://www.nationalrail.co.uk/). WAT for London Waterloo
4. Add a new national rail integration in your home assistant using the info collected above

# Integration within the UI

TBD

# Fair use policy

National Rail limits API call to five million requests per four week railway period.
An update every minute for a 4 week period would require 40,320 request. You could therefore have 124 those sensors.

We are currently refreshing once every 10 minutes