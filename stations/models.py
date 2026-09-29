from django.db import models


class Station(models.Model):
    COUNTRIES = [("US", "United States"), ("CA", "Canada")]

    opis_id = models.PositiveIntegerField(primary_key=True)
    name = models.CharField(max_length=120)
    address = models.CharField(max_length=200)
    city = models.CharField(max_length=80)
    state = models.CharField(max_length=2)
    country = models.CharField(max_length=2, choices=COUNTRIES)
    rack_id = models.PositiveIntegerField()
    price = models.DecimalField(
        max_digits=12, decimal_places=8, help_text="USD per gallon, lowest seen for this ID."
    )
    lat = models.FloatField()
    lng = models.FloatField()
    radius_miles = models.FloatField(help_text="Approximate radius of the city the station is placed in.")
    location_precision = models.CharField(max_length=10, default="city")
    geocode_source = models.CharField(max_length=20)

    class Meta:
        ordering = ["opis_id"]

    def __str__(self):
        return f"{self.name} ({self.city}, {self.state})"
