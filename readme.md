# Contrôleur ESP32 pour Train LEGO Duplo (BLE)

Ce projet permet de piloter un **train à vapeur (10874) ou un train de marchandises (10875) LEGO Duplo** via Bluetooth (BLE) à l'aide d'un boîtier physique basé sur un **ESP32** sous MicroPython.

L'approche privilégie le jeu tactile et analogique sans écran.

## 🚀 Nouvelles Fonctionnalités (v2026)

- **Gestion Intelligente de la Vitesse** : Courbe de puissance entre 25% et 100% (avant/arrière) avec zone morte centrale calculée automatiquement.
- **Sécurité "Enfant" (Combo Lock)** : Possibilité de verrouiller les boutons pour ne laisser que le potentiomètre actif.
- **Modes Lumineux Avancés** : Gestion du ON/OFF général, cycle de 7 couleurs, double saut de couleur et mode arc-en-ciel.
- **Calibration Persistante** : Sauvegarde des bornes du potentiomètre dans un fichier `pot_config.json` pour survivre au redémarrage.
- **Économie d'Énergie** : Mise en veille profonde (**Deep Sleep**) après 30 min d'inactivité ou déconnexion prolongée.
- **Monitoring Batterie** : Lecture du voltage via le port dédié (GPIO 35).

---

## 🛠 Matériel & Branchement (Pinout)

| Composant          | Broche ESP32 | Fonction / État Logique                |
| :----------------- | :----------- | :------------------------------------- |
| **Potentiomètre**  | GPIO 33      | Vitesse (ADC) + Réveil Deep Sleep      |
| **Bouton Frein**   | GPIO 13      | Arrêt / Config (Pull-up interne)       |
| **Bouton Klaxon**  | GPIO 5       | Sons multi-fonctions (Pull-up)         |
| **Bouton Lumière** | GPIO 2       | Cycles lumineux (Pull-up)              |
| **Bouton Fuel**    | GPIO 19      | Ravitaillement / Arrêt (Pull-up)       |
| **LED Status**     | GPIO 22      | Fixe = Connecté / Éteint = Recherche   |
| **Lecture Bat.**   | GPIO 35      | Mesure tension batterie (Div. tension) |

---

## 🎮 Guide des Commandes

### 🕹 Le Potentiomètre (Vitesse & Sécurité)

- **Déverrouillage** : Au démarrage, par sécurité, le moteur est inactif. Il faut bouger le potentiomètre de façon significative pour "réveiller" le moteur.
- **Plage de puissance** : Le train démarre à **25%** dès la sortie de la zone morte et monte jusqu'à **100%** (linéaire par paliers de 5%).
- **Zone Morte** : Une marge de sécurité est appliquée au centre pour garantir l'arrêt complet.

### 🛑 Bouton Frein (GPIO 13)

- **Appui simple** : Arrêt immédiat, phares **Rouges** et son de freinage.
- **Appui long (10s)** : Entre en **Mode Configuration** (Calibration du potentiomètre).

### 🔊 Bouton Klaxon (GPIO 5)

- **Appui simple** : Klaxon standard (Lumière jaune).
- **Double appui rapide** : Annonce de départ en gare (Lumière verte + son "Départ").
- **Appui long (>600ms)** : Sifflet à vapeur long avec effet de lumière blanche vacillante.

### 💡 Bouton Lumière (GPIO 2)

- **Appui long (>800ms)** : Allumage ou extinction complète des phares (Master Switch).
- **Appui simple (si ON)** : Passe à la couleur suivante dans la liste (Blanc, Rouge, Vert, Bleu, Jaune, Violet, Cyan).
- **Double appui rapide** :
  - _Si lumières éteintes_ : Active/Désactive le **Mode Arc-en-ciel** (défilement automatique).
  - _Si lumières allumées_ : Effectue un **double saut** dans la liste des couleurs.

La séquence de couleur est la suivante :

| Ordre | Couleur | Code Hex |
| :---- | :------ | :------- |
| 1     | BLANC   | 0x0A     |
| 2     | ROUGE   | 0x09     |
| 3     | VERT    | 0x06     |
| 4     | BLEU    | 0x03     |
| 5     | JAUNE   | 0x07     |
| 6     | VIOLET  | 0x02     |
| 7     | CYAN    | 0x05     |

### ⛽ Bouton Fuel / Eau (GPIO 19)

- **Action** : Arrête le train (freinage), passe le phare en bleu, joue le son de remplissage d'eau et fait scintiller la lumière pendant 3,5 secondes.

### 🔐 Verrouillage des boutons (Combo Lock)

- **Action** : Maintenez les **4 boutons enfoncés simultanément**.
- **Effet** : Alterne entre le mode normal et le mode "Boutons verrouillés". Pratique pour éviter que les enfants ne saturent le Bluetooth de commandes sonores !

---

## ⚙️ Configuration & Calibration

Si votre potentiomètre ne répond pas sur toute sa course ou si le point zéro est décalé :

1. Maintenez **FREIN** pendant 10 secondes (le train devient blanc).
2. **ÉTAPE 1** : Mettez le potentiomètre au **MAX** (en haut) et appuyez sur **KLAXON**. (Lumière Jaune).
3. **ÉTAPE 2** : Mettez le potentiomètre au **MIN** (en bas) et appuyez sur **LUMIÈRE**. (Lumière Bleue).
4. Le train clignote en blanc : les valeurs sont sauvegardées dans la mémoire flash de l'ESP32.

---

## 💻 Installation rapide

1. Installez MicroPython sur votre ESP32.
2. Installez la bibliothèque nécessaire via Thonny ou la console :

   ```python
   import mip
   mip.install("aioble")
   ```

3. Téléversez le fichier main.py.
4. Le train doit être allumé pour que l'ESP32 le détecte automatiquement (le nom du train doit être "Train" ou posséder l'UUID LEGO LWP3).

_Note : Le système surveille l'inactivité. Si vous n'utilisez pas le contrôleur pendant 30 minutes, il s'éteint pour préserver la batterie. Tournez le potentiomètre pour le réveiller._
