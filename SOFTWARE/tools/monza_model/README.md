# Monza model (AI Model page)

How `SOFTWARE/dashboard/lib/monza-model.json` was made. Run from this folder:

    g++ -std=c++17 -O1 -I../../../HARDWARE/esp32/main_updated_corrected__1_ gen_monza.cpp -o gen
    ./gen 12 > monza_laps.csv          # 12 laps of Monza (track 12) driven by the real VCU logic, 20 Hz
    python3 train_monza.py monza_laps.csv ../../dashboard/lib/monza-model.json

Inputs: lap position (Fourier terms) and rear wheel speed. Outputs: motor duty %, steering deg (+ = left),
straight mode, speed. The training data is the VCU's own circuit run, so the model imitates the stored Monza
map. To train on real laps instead, write a CSV with the same columns from logged telemetry and rerun the
trainer. Retrain if the VCU's lap length (60 m), duty cap/floor or the track map changes.
