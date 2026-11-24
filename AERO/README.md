# Aerodynamics

Asymmetric active aero is the project's research feature: four independently driven flaps, with the inside pair dropping toward flat in a corner and DRS flattening all four.

Suggested contents (add what you have):

```
AERO/
├── README.md               set-up summary and key results (this file)
├── cfd/
│   ├── setup.md            solver (STAR-CCM+), domain, mesh size, turbulence model, inlet speed, flap angles run
│   ├── results.csv         one row per case: flap angles, Cl, Cd, downforce (N), drag (N)
│   └── images/             pressure contours, streamlines, mesh close-ups (PNG)
└── flap_law.md             how the CFD results became the firmware's flap law and the twin's aero map
```

Rules of thumb:

- Commit **results** (CSV, PNG, a short write-up), not solver files: `.sim` / `.cas` files are hundreds of MB and are ignored by `.gitignore`.
- State the operating point next to every number (speed, ride height, flap angle).
- Say plainly which numbers are simulated. On the car, flap angles are measured (rear pot feedback); downforce and drag are modelled.
